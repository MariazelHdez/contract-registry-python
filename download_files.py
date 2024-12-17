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
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Set environment variable to skip Chromium download
os.environ['PYPPETEER_SKIP_CHROMIUM_DOWNLOAD'] = 'True'

apikey = "2c33ca4e0cc4ad9ec06f50e8c4a3eea9"  # Replace with your actual 2Captcha API key

download_dir = os.path.join(os.getcwd(), "downloads_script")
if not os.path.exists(download_dir):
    os.makedirs(download_dir)

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

                    stop_script_flag['stop'] = True

                    captcha_solved_event.set()
            except Exception as e:
                print("An error occurred while solving Captcha:", e)

                stop_script_flag['stop'] = True
                captcha_solved_event.set()
        else:
            return

    page.on('console', lambda msg: asyncio.ensure_future(console_message_handler(msg)))

async def download_report(page, from_year, to_year):
    print(f"Attempting to select fiscal years: From {from_year} to {to_year}")

    # Click and select "From" year
    await page.click('#P500_FISCAL_YEAR_FROM')
    await page.evaluate(
        '''(year) => {
            const fromSelect = document.querySelector('#P500_FISCAL_YEAR_FROM');
            fromSelect.value = year;
            fromSelect.dispatchEvent(new Event('change', { bubbles: true }));
        }''',
        from_year
    )
    print(f"Selected 'From' year: {from_year}")

    # Wait for the "To" options to update based on "From" selection
    await asyncio.sleep(2)  # Short delay to ensure options refresh

    # Click and select "To" year after verifying it's available
    to_options = await page.Jeval('#P500_FISCAL_YEAR_TO', '(el) => Array.from(el.options).map(o => o.value)')
    if to_year in to_options:
        await page.click('#P500_FISCAL_YEAR_TO')
        await page.evaluate(
            '''(year) => {
                const toSelect = document.querySelector('#P500_FISCAL_YEAR_TO');
                toSelect.value = year;
                toSelect.dispatchEvent(new Event('change', { bubbles: true }));
            }''',
            to_year
        )
        print(f"Selected 'To' year: {to_year}")
    else:
        print(f"'To' year {to_year} not available after selecting 'From' year {from_year}.")
        return False

    # Click the "Search" button
    search_button_selector = '#B106150366531214971'
    await page.waitForSelector(search_button_selector, {'timeout': 20000})
    await page.click(search_button_selector)
    print("Clicked the search button.")
    
    # Wait for the download button to be available after results load
    download_button_selector = '#B47528447156014705'
    try:
        await page.waitForSelector(download_button_selector, {'timeout': 60000})
    except asyncio.TimeoutError:
        print("Timed out waiting for download button. Skipping this fiscal year range.")
        return False
    await asyncio.sleep(10)
    # Click the download button
    await page.click(download_button_selector)
    print("Clicked the download button.")
    await asyncio.sleep(10)  # Adjust based on download size or implement a more reliable download completion check
    
    # Rename downloaded file with fiscal year and timestamp
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    file_name = f"{from_year}-{to_year}-{timestamp}.csv"
    file_path = os.path.join(download_dir, file_name)
    downloaded_file_path = os.path.join(download_dir, "DownloadedFileName.csv")  # Adjust this based on actual name

    # Rename if download completed
    if os.path.exists(downloaded_file_path):
        os.rename(downloaded_file_path, file_path)
        print(f"Downloaded and saved file as: {file_path}")
    else:
        print("File download failed.")
    
    return True

async def interactions_reports(page, captcha_solved_event, stop_script_flag, browser):
    fiscal_years = [
        "2007-08", "2008-09", "2009-10", "2010-11", "2011-12",
        "2012-13", "2013-14", "2014-15", "2015-16", "2016-17",
        "2017-18", "2018-19", "2019-20", "2020-21", "2021-22",
        "2022-23", "2023-24", "2024-25"
    ]

    for i in range(len(fiscal_years) - 1):
        from_year = fiscal_years[i]
        to_year = fiscal_years[i + 1]

        print(f"Processing fiscal year range: From {from_year} to {to_year}")

        # Check if CAPTCHA is present and resolve if necessary
        captcha_present = await page.evaluate('''() => !!document.querySelector('#captcha-element-id')''')
        
        if captcha_present:
            print("CAPTCHA detected. Solving...")
            await setup_captcha_handling(page, captcha_solved_event, stop_script_flag)
            
            # Wait until CAPTCHA is solved
            await captcha_solved_event.wait()
            captcha_solved_event.clear()
            print("CAPTCHA solved.")
        
            # Refresh the page after CAPTCHA solution
            await page.goto('https://service.yukon.ca/apps/contract-registry', {'waitUntil': 'networkidle2'})
            await asyncio.sleep(5)  # Adjust delay for page reload

        # Proceed with the report download if CAPTCHA is not stopping the script
        if stop_script_flag.get('stop'):
            print("Script stopped due to CAPTCHA failure or other issue.")
            break

        # Attempt to download the report for the specified fiscal years
        success = await download_report(page, from_year, to_year)

        if not success:
            print(f"Skipping fiscal year range {from_year} to {to_year} due to an issue.")
            break

        #await interactions(page, captcha_solved_event, stop_script_flag, browser, from_year, to_year)

        # Go back to the main page after each download attempt
        await page.goto('https://service.yukon.ca/apps/contract-registry', {'waitUntil': 'networkidle2'})
        await asyncio.sleep(5)  # Adjust delay for page reload

    print("Finished processing all fiscal years.")

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
            await setup_captcha_handling(detail_page, detail_captcha_solved_event)

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
        
async def main():

    possible_paths = [
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
    	'/usr/bin/google-chrome',  # Common path for Linux systems
        '/usr/local/bin/google-chrome',  # Alternative path in some Linux distributions
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',  # Mac path
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
    await page.goto('https://service.yukon.ca/apps/contract-registry', waitUntil='networkidle2')

    # Solve captcha again
    try:
        await asyncio.wait_for(page_ready_event.wait(), timeout=10)
    except asyncio.TimeoutError:
        logger.error("Timed out waiting for the page to be ready. Retrying...")
        await page.reload({'waitUntil': 'networkidle2'})


    # await interactions(page, captcha_solved_event, stop_script_flag, browser)
    await interactions_reports(page, captcha_solved_event, stop_script_flag, browser)
    

    await browser.close()

if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
