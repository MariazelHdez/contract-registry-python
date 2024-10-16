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
from pyppeteer.errors import NetworkError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Set environment variable to skip Chromium download
os.environ['PYPPETEER_SKIP_CHROMIUM_DOWNLOAD'] = 'True'

apikey = "49d57f37aa02dc2135a7b3bc8ff4a1a3"

captcha_solved_event = asyncio.Event()
page_ready_event = asyncio.Event()

async def main():
    # Find Chrome executable path
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

    # Apply stealth techniques
    await stealth(page)

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

    # Handle console messages to catch 'intercepted-params'
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
                # Retrieve Captcha solution
                while True:
                    solution = requests.get(
                        f"https://2captcha.com/res.php?key={apikey}&action=get&json=1&id={captcha_id}"
                    ).json()
                    if solution["request"] == "CAPCHA_NOT_READY":
                        print("Captcha not ready, waiting...")
                        time.sleep(5)
                    elif "ERROR" in solution["request"]:
                        print("Error:", solution["request"])
                        break
                    else:
                        print("Captcha Solved:", solution)
                        break

                # Pass the Captcha solution back to the page
                await page.evaluate('cfCallback', solution["request"])

                # Signal that the Captcha has been solved
                captcha_solved_event.set()

                # Wait for the page to reload after solving the Captcha
                await page.waitForNavigation({'waitUntil': 'networkidle2', 'timeout': 60000})
                page_ready_event.set()

            except Exception as e:
                print("An error occurred while solving Captcha:", e)
                await browser.close()
        else:
            return

    # Attach the console message handler
    page.on('console', lambda msg: asyncio.ensure_future(console_message_handler(msg)))

    # Go to the initial page
    await page.goto('https://service.yukon.ca/apps/contract-registry', waitUntil='networkidle2')

    # If the Captcha appears, wait for it to be solved and page to reload
    if not page_ready_event.is_set():
        print("Waiting for page to be ready...")
        await page_ready_event.wait()

    await asyncio.sleep(5)

    await interactions(page)

    await browser.close()

async def interactions(page):
    try:
        data_storage = []

        while True:

            pages = await page.browser.pages()
            page = pages[-1]


            await page.waitForSelector('#B106150366531214971', timeout=60000)

            await page.click('#B106150366531214971')
            print("Submit button clicked.")

            await asyncio.sleep(5)

            # Wait for the table to load
            await page.waitForSelector('#report_table_P510_RESULTS', timeout=80000)
            logger.info("Table loaded.")

            # Collect all the rows data into a list, including detail URLs
            row_data_list = []

            # Build the row_data_list with data and detail URLs
            rows = await page.querySelectorAll('#report_table_P510_RESULTS tbody tr')
            for row in rows:
                cells = await row.querySelectorAll('td')
                row_data = {}
                detail_url = None
                for cell in cells:
                    # Get the header ID from the 'headers' attribute
                    header_id = await page.evaluate('(cell) => cell.getAttribute("headers")', cell)
                    # Get the cell text
                    cell_text = await page.evaluate('(cell) => cell.innerText.trim()', cell)
                    # Store the cell data with header ID as key
                    row_data[header_id] = cell_text

                    # Check if this cell contains the detail link (in 'FISCAL_YR' cell)
                    if header_id == 'FISCAL_YR':
                        link_element = await cell.querySelector('a')
                        if link_element:
                            href = await page.evaluate('(a) => a.getAttribute("href")', link_element)
                            detail_url = urljoin(page.url, href)
                row_data['detail_url'] = detail_url
                row_data_list.append(row_data)

            # Open a single detail page to reuse
            detail_page = await page.browser.newPage()
            await detail_page.setUserAgent(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/115.0.0.0 Safari/537.36'
            )
            await stealth(detail_page)

            # Share cookies and session storage
            client = await page.target.createCDPSession()
            cookies = await client.send('Network.getAllCookies')
            await client.detach()

            client = await detail_page.target.createCDPSession()
            await client.send('Network.setCookies', {'cookies': cookies['cookies']})
            await client.detach()

            # Now process each row by detail_url
            for index, row_data in enumerate(row_data_list):
                try:
                    contract_no = row_data.get('Contract Number')
                    amount = row_data.get('Contract Amount')
                    detail_url = row_data.get('detail_url')
                    logger.info(f"Processing Contract No.: {contract_no}, Amount: {amount}")

                    if not detail_url:
                        logger.warning(f"No detail URL found for Contract No.: {contract_no}")
                        continue


                    response = await detail_page.goto(detail_url)
                    await asyncio.sleep(2)


                    if response.status == 403:
                        logger.warning("Encountered Captcha, waiting for it to be solved...")
                        # Wait for Captcha to be solved
                        await captcha_solved_event.wait()
                        # Captcha solved, retry navigation
                        response = await detail_page.goto(detail_url)
                        # Reset the event for future Captcha challenges
                        captcha_solved_event.clear()

                    if response.status != 200:
                        logger.error(f"Failed to load detail page for Contract No.: {contract_no}, HTTP status: {response.status}")
                        continue

                    logger.info("Navigated to detail page.")
                    await asyncio.sleep(4)

                    # Wait for the details page to load
                    await detail_page.waitForSelector('#P520_DESCRIPTION_CONTAINER', timeout=60000)
                    logger.info("Details page loaded.")
                    await asyncio.sleep(3)

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
                            # Extract label and description from the container
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
                    # Store the extracted details
                        if key in details:
                            row_data[key] = details[key]

                    # Remove 'detail_url' from 'row_data' if it's not needed
                    if 'detail_url' in row_data:
                        del row_data['detail_url']

                    # Optionally, if 'details' is no longer needed, we can skip assigning it to 'row_data'
                    data_storage.append(row_data)

                    logger.info(f"Processed and stored details for Contract No.: {contract_no}")

                    # Add a delay to avoid rate limiting
                    await asyncio.sleep(4)

                except Exception as e:
                    logger.error(f"Exception during processing of Contract No.: {contract_no}: {e}")
                    traceback.print_exc()
                    continue


            await detail_page.close()
            logger.info("Closed detail page.")


            next_button = await page.querySelector('.t-Report-paginationLink--next')
            if next_button:
                await next_button.click()
                logger.info("Navigating to next page...")
                await page.waitForNavigation({'waitUntil': 'networkidle2', 'timeout': 60000})
                await asyncio.sleep(5)  # Add a small delay to ensure the next page is fully loaded
            else:
                logger.info("No more pages left to process.")
                break

        logger.info("Finished processing all rows on all pages.")
        logger.info("Extracted Table Data:")
        logger.info(json.dumps(data_storage, indent=2))

        await page.screenshot({'path': 'after_interaction.png'})

    except Exception as e:
        logger.error("An error occurred during interactions: %s", e)
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())