import asyncio
import os
import json
import logging
import traceback
import time
import requests
from urllib.parse import urljoin
from pyppeteer import launch
from pyppeteer_stealth import stealth
from pyppeteer.errors import NetworkError, TimeoutError
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# Read API key and webpage URL from environment variables
apikey = os.getenv('API_KEY')
webpage_url = os.getenv('WEBPAGE_URL')

if not apikey:
    print("API_KEY not found in environment variables. Please set it in your .env file.")
    exit(1)

if not webpage_url:
    print("WEBPAGE_URL not found in environment variables. Please set it in your .env file.")
    exit(1)

# Set environment variable to skip Chromium download
os.environ['PYPPETEER_SKIP_CHROMIUM_DOWNLOAD'] = 'True'

async def setup_captcha_handling(page, captcha_solved_event, stop_script_flag, page_ready_event=None, set_stop_script_on_error=True):
    # Inject JavaScript to intercept Cloudflare's Turnstile Captcha
    await page.evaluateOnNewDocument(
        """
        () => {
          console.clear = () => console.log('Console was cleared')
          const i = setInterval(() => {
              if (window.turnstile) {
                  clearInterval(i)
                  window.turnstile.render = (a, b) => {
                      let params = {
                          sitekey: b.sitekey,
                          pageurl: window.location.href,
                          data: b.cData,
                          pagedata: b.chlPageData,
                          action: b.action,
                          userAgent: navigator.userAgent,
                          json: 1
                      }
                      // We will intercept this message in Puppeteer
                      console.log('intercepted-params:' + JSON.stringify(params))
                      window.cfCallback = b.callback
                      return
                  }
              }
          }, 50)
        }
        """
    )

    # Attach the console message handler
    async def console_message_handler(msg):
        txt = msg.text
        if 'intercepted-params:' in txt:
            params = json.loads(txt.replace('intercepted-params:', ''))
            print("Intercepted Params:", params)
            try:
                # Prepare payload for 2Captcha
                payload = {
                    "key": apikey,
                    "method": "turnstile",
                    "sitekey": params["sitekey"],
                    "pageurl": params["pageurl"],
                    "data": params["data"],
                    "pagedata": params["pagedata"],
                    "action": params["action"],
                    "useragent": params["userAgent"],
                    "json": 1,
                }
                # Send Captcha to 2Captcha
                response = requests.post("https://2captcha.com/in.php", data=payload)
                print("Captcha sent")
                captcha_id = response.json()["request"]
                time.sleep(2)
                retries = 0
                max_retries = 10

                while retries < max_retries:
                    solution = requests.get(
                        f"https://2captcha.com/res.php?key={apikey}&action=get&json=1&id={captcha_id}"
                    ).json()
                    if solution["request"] == "CAPCHA_NOT_READY":
                        print("Captcha not ready, waiting...")
                        time.sleep(5)
                        retries += 1
                    elif "ERROR" in solution["request"]:
                        print("Error:", solution["request"])
                        break
                    else:
                        print("Captcha Solved:", solution)

                        await page.evaluate('cfCallback', solution["request"])

                        captcha_solved_event.set()

                        if page_ready_event:
                            page_ready_event.set()
                        return
                else:
                    print("Failed to solve CAPTCHA after multiple attempts.")

                    if set_stop_script_on_error:
                        stop_script_flag['stop'] = True

                    captcha_solved_event.set()
            except Exception as e:
                print("An error occurred while solving Captcha:", e)

                if set_stop_script_on_error:
                    stop_script_flag['stop'] = True

                captcha_solved_event.set()
        else:
            return

    page.on('console', lambda msg: asyncio.ensure_future(console_message_handler(msg)))

async def process_row(row_data, browser, stop_script_flag, data_storage, invalid_contracts, semaphore):
    async with semaphore:
        if stop_script_flag.get('stop'):
            logger.info("Stopping script due to CAPTCHA failure.")
            return

        try:
            contract_no = row_data.get('Contract Number')
            amount = row_data.get('Contract Amount')
            detail_url = row_data.get('detail_url')
            logger.info(f"Processing Contract No.: {contract_no}, Amount: {amount}")

            if not detail_url:
                logger.warning(f"No detail URL found for Contract No.: {contract_no}")
                return

            # Open a new page for this task
            detail_page = await browser.newPage()
            await detail_page.setUserAgent(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/115.0.0.0 Safari/537.36'
            )
            await stealth(detail_page)

            # Create a new captcha_solved_event for detail_page
            detail_captcha_solved_event = asyncio.Event()
            await setup_captcha_handling(detail_page, detail_captcha_solved_event, stop_script_flag, set_stop_script_on_error=False)

            max_retries = 5
            retries = 0
            while retries < max_retries:
                try:
                    response = await detail_page.goto(detail_url, {'timeout': 60000})
                except TimeoutError:
                    logger.error(f"Timeout while loading detail page for Contract No.: {contract_no}")
                    retries += 1
                    continue

                # Get page content to check for CAPTCHA indicators
                page_content = await detail_page.content()

                if 'cf-turnstile' in page_content or 'Cloudflare' in page_content:
                    logger.warning(f"Encountered CAPTCHA on detail page for Contract No.: {contract_no}, waiting for it to be solved...")

                    await detail_captcha_solved_event.wait()

                    # Wait for navigation after solving the CAPTCHA
                    try:
                        await detail_page.waitForNavigation({'waitUntil': 'networkidle0', 'timeout': 60000})
                    except TimeoutError:
                        logger.error(f"Timeout waiting for navigation after solving CAPTCHA for Contract No.: {contract_no}")
                        await detail_page.close()
                        return

                    if stop_script_flag.get('stop'):
                        logger.info("Stopping script due to CAPTCHA failure.")
                        await detail_page.close()
                        return

                    detail_captcha_solved_event.clear()
                    retries += 1
                    continue  # Retry the navigation
                elif response.status == 403:
                    logger.error(f"Server-side 403 error on detail page for Contract No.: {contract_no}. Skipping this contract.")

                    # Append the contract number to invalid_contracts
                    invalid_contracts.append(contract_no)

                    await detail_page.close()
                    return
                elif response.status != 200:
                    logger.error(f"Failed to load detail page for Contract No.: {contract_no}, HTTP status: {response.status}")
                    retries += 1
                    await asyncio.sleep(4)
                    continue  # Retry the navigation
                else:
                    # Page loaded successfully without CAPTCHA
                    break
            else:
                logger.error(f"Failed to load detail page for Contract No.: {contract_no} after {max_retries} attempts.")
                await detail_page.close()
                return

            logger.info("Navigated to detail page.")

            # Wait for the details page to load
            await detail_page.waitForSelector('#P520_DESCRIPTION_CONTAINER', timeout=60000)
            logger.info("Details page loaded.")

            # Extract details
            details = {}

            detail_fields = [
                ('P520_DESCRIPTION_LABEL', 'P520_DESCRIPTION'),
                ('P520_DEPARTMENT_LABEL', 'P520_DEPARTMENT'),
                ('P520_PROJECT_MANAGER_LABEL', 'P520_PROJECT_MANAGER'),
                ('P520_WORK_COMMUNITY_LABEL', 'P520_WORK_COMMUNITY'),
                ('P520_POSTAL_CODE_LABEL', 'P520_POSTAL_CODE'),
                ('P520_YUKON_BUSINESS_LABEL', 'P520_YUKON_BUSINESS'),
                ('P520_YFN_BUSINESS_LABEL', 'P520_YFN_BUSINESS'),
                ('P520_CONTRACT_TYPE_LABEL', 'P520_CONTRACT_TYPE'),
                ('P520_TENDER_TYPE_LABEL', 'P520_TENDER_TYPE'),
                ('P520_TENDER_CLASS_LABEL', 'P520_TENDER_CLASS'),
                ('P520_SOA_NUMBER_LABEL', 'P520_SOA_NUMBER')
            ]

            for container_id in [f'{field[0][:-6]}_CONTAINER' for field in detail_fields]:
                try:
                    title = await detail_page.evaluate(f'''
                        () => {{
                            const container = document.getElementById('{container_id}');
                            if (container) {{
                                const label = container.querySelector('label');
                                return label ? label.innerText.trim() : null;
                            }}
                            return null;
                        }}
                    ''')
                    description = await detail_page.evaluate(f'''
                        () => {{
                            const container = document.getElementById('{container_id}');
                            if (container) {{
                                const span = container.querySelector('span');
                                return span ? span.innerText.trim() : null;
                            }}
                            return null;
                        }}
                    ''')
                    if title and description:
                        details[title] = description
                except Exception as e:
                    logger.error(f'Could not extract details from container {container_id}: {e}')

            # Move desired keys from 'details' to 'row_data'
            desired_keys = ['Project Manager/Buyer', 'Postal Code', 'Yukon Business', 'Yukon First Nations Business', 'Tender Type']

            for key in desired_keys:
                if key in details:
                    row_data[key] = details[key]

            # Remove 'detail_url' from 'row_data' if it's not needed
            if 'detail_url' in row_data:
                del row_data['detail_url']

            data_storage.append(row_data)

            logger.info(f"Processed and stored details for Contract No.: {contract_no}")
            await detail_page.close()

        except Exception as e:
            logger.error(f"Exception during processing of Contract No.: {contract_no}: {e}")
            traceback.print_exc()
            await detail_page.close()
            return

async def interactions(page, captcha_solved_event, stop_script_flag, browser, fiscal_year_from, fiscal_year_to):
    try:
        data_storage = []
        invalid_contracts = []
        page_counter = 0  # Counter for processed pages

        # Limit the number of concurrent tasks
        semaphore = asyncio.Semaphore(3)  # Adjusted the number to reduce concurrency

        while True:
            if stop_script_flag.get('stop'):
                logger.info("Stopping script due to CAPTCHA failure.")
                break

            page_counter += 1
            logger.info(f"Processing page number: {page_counter}")

            table_selector = '#report_table_P510_RESULTS'
            table_exists = await page.querySelector(table_selector) is not None

            if not table_exists:
                submit_button_selector = '#B106150366531214971'
                submit_button = await page.querySelector(submit_button_selector)
                if submit_button is not None:
                    await page.waitForSelector(submit_button_selector, timeout=60000)
                    await page.click(submit_button_selector)
                    print("Submit button clicked.")

                    await page.waitForSelector(table_selector, timeout=80000)
                    logger.info("Table loaded.")
                else:
                    logger.error("Neither table nor submit button found on the page.")
                    await asyncio.sleep(6)
                    if stop_script_flag.get('stop'):
                        logger.info("Stopping script due to CAPTCHA failure.")
                        break
                    continue
            else:
                logger.info("Table loaded.")

            row_data_list = []

            # Get header IDs in order
            header_cells = await page.querySelectorAll('#report_table_P510_RESULTS thead th')
            header_ids = []
            for header_cell in header_cells:
                header_id = await page.evaluate('(cell) => cell.getAttribute("id")', header_cell)
                header_ids.append(header_id)

            rows = await page.querySelectorAll('#report_table_P510_RESULTS tbody tr')
            for row in rows:
                cells = await row.querySelectorAll('td')
                row_data = {}
                detail_url = None
                for index, cell in enumerate(cells):
                    header_id = header_ids[index]
                    cell_text = await page.evaluate('(cell) => cell.innerText.trim()', cell)
                    row_data[header_id] = cell_text

                    if not detail_url:
                        link_element = await cell.querySelector('a')
                        if link_element:
                            href = await page.evaluate('(a) => a.getAttribute("href")', link_element)
                            detail_url = urljoin(page.url, href)
                row_data['detail_url'] = detail_url
                row_data_list.append(row_data)

            tasks = []
            for row_data in row_data_list:
                task = asyncio.ensure_future(process_row(
                    row_data, browser, stop_script_flag, data_storage, invalid_contracts, semaphore))
                tasks.append(task)

            await asyncio.gather(*tasks)

            logger.info(f"Finished processing page number: {page_counter}")

            # Save data to JSON file after each page
            try:
                filename = f"{fiscal_year_from}_{fiscal_year_to}_data.json"
                with open(filename, 'w') as f:
                    json.dump(data_storage, f, indent=2)
                logger.info(f"Data saved to {filename} after processing page {page_counter}")

                # Save invalid contracts to JSON file after each page
                invalid_filename = f"{fiscal_year_from}_{fiscal_year_to}_invalid_contracts.json"
                # Save as a list
                with open(invalid_filename, 'w') as f:
                    json.dump(invalid_contracts, f, indent=2)
                logger.info(f"Invalid contracts saved to {invalid_filename} after processing page {page_counter}")

            except Exception as e:
                logger.error(f"Error saving data to JSON file: {e}")

            if stop_script_flag.get('stop'):
                logger.info("Stopping script due to CAPTCHA failure.")
                break

            next_button_selector = '.t-Report-paginationLink--next'
            next_button = await page.querySelector(next_button_selector)
            if next_button:
                await next_button.click()
                logger.info("Navigating to next page...")

                try:
                    await page.waitForNavigation({'waitUntil': 'networkidle2', 'timeout': 60000})
                except TimeoutError:
                    logger.error("Timeout while waiting for next page to load.")

                captcha_solved_event.clear()

                continue
            else:
                logger.info("No more pages left to process.")
                break

        logger.info("Finished processing all rows on all pages.")
        logger.info("Extracted Table Data:")
        # Show data array
        # logger.info(json.dumps(data_storage, indent=2))

        await page.screenshot({'path': 'after_interaction.png'})

    except Exception as e:
        logger.error("An error occurred during interactions: %s", e)
        traceback.print_exc()

async def main():

    possible_paths = [
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
    ]

    chrome_path = None
    for path in possible_paths:
        if os.path.exists(path):
            chrome_path = path
            break

    if not chrome_path:
        print("Chrome executable not found. Please check your installation.")
        return

    print(f"Using Chrome executable at: {chrome_path}")

    browser = await launch(
        executablePath=chrome_path,
        headless=False,
        devtools=False,
        autoClose=False,
        args=[
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-blink-features=AutomationControlled',
        ]
    )
    page = await browser.newPage()

    # Set a realistic User-Agent
    await page.setUserAgent(
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/115.0.0.0 Safari/537.36'
    )

    await stealth(page)

    captcha_solved_event = asyncio.Event()
    page_ready_event = asyncio.Event()
    stop_script_flag = {'stop': False}

    await setup_captcha_handling(page, captcha_solved_event, stop_script_flag, page_ready_event)

    # Go to the initial page
    await page.goto(webpage_url, waitUntil='networkidle2')

    # Solve captcha again
    try:
        await asyncio.wait_for(page_ready_event.wait(), timeout=120)
    except asyncio.TimeoutError:
        logger.error("Timed out waiting for the page to be ready. Retrying...")
        await page.reload({'waitUntil': 'networkidle2'})

    # Wait for the select element P500_FISCAL_YEAR_FROM to be available
    await page.waitForSelector('#P500_FISCAL_YEAR_FROM', timeout=60000)

    # Get all options from P500_FISCAL_YEAR_FROM
    from_options = await page.querySelectorAll('#P500_FISCAL_YEAR_FROM option')

    # Get the last option's value and text
    last_from_option = from_options[-1]
    last_from_option_value = await page.evaluate('(option) => option.value', last_from_option)
    last_from_option_text = await page.evaluate('(option) => option.textContent.trim()', last_from_option)

    # Select the last option in P500_FISCAL_YEAR_FROM
    await page.select('#P500_FISCAL_YEAR_FROM', last_from_option_value)
    logger.info(f"Selected last option in P500_FISCAL_YEAR_FROM: {last_from_option_text}")

    # Wait for P500_FISCAL_YEAR_TO options to load (since it's dependent on the first select)
    await page.waitForFunction('document.querySelectorAll("#P500_FISCAL_YEAR_TO option").length > 0', timeout=60000)

    # Get all options from P500_FISCAL_YEAR_TO
    to_options = await page.querySelectorAll('#P500_FISCAL_YEAR_TO option')

    # Get the first option's value and text
    first_to_option = to_options[0]
    first_to_option_value = await page.evaluate('(option) => option.value', first_to_option)
    first_to_option_text = await page.evaluate('(option) => option.textContent.trim()', first_to_option)

    # Select the first option in P500_FISCAL_YEAR_TO
    await page.select('#P500_FISCAL_YEAR_TO', first_to_option_value)
    logger.info(f"Selected first option in P500_FISCAL_YEAR_TO: {first_to_option_text}")

    # Store the selected fiscal years, replacing any problematic characters
    fiscal_year_from = last_from_option_text.replace('/', '-')
    fiscal_year_to = first_to_option_text.replace('/', '-')

    await interactions(page, captcha_solved_event, stop_script_flag, browser, fiscal_year_from, fiscal_year_to)

    await browser.close()

if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
