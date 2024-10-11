import asyncio
import os
import json
PYPPETEER_CHROMIUM_REVISION = '1263111'
os.environ['PYPPETEER_CHROMIUM_REVISION'] = PYPPETEER_CHROMIUM_REVISION
from pyppeteer import launch
import time
import requests
from pyppeteer_stealth import stealth
import logging
import traceback

apikey = "49d57f37aa02dc2135a7b3bc8ff4a1a3"

async def main():
    browser = await launch(
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

    # Executing the JavaScript code on the page
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
                      // we will intercept the message in puppeteer
                      console.log('intercepted-params:' + JSON.stringify(params))
                      window.cfCallback = b.callback
                      return
                  }
              }
          }, 50)
        }
        """
    )

    # Intercept console messages to catch a message containing 'intercepted-params:'
    async def console_message_handler(msg):
        txt = msg.text
        print(f"Console message: {txt}")
        if 'intercepted-params:' in txt:
            params = json.loads(txt.replace('intercepted-params:', ''))
            print("Intercepted Params:", params)
            try:
                # Captcha params
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
                # Send captcha to 2captcha
                response = requests.post("https://2captcha.com/in.php", data=payload)
                print("Captcha sent")
                print(response.text)
                captcha_id = response.json()["request"]
                time.sleep(2)
                # Getting a captcha response
                while True:
                    solution = requests.get(
                        f"https://2captcha.com/res.php?key={apikey}&action=get&json=1&id={captcha_id}"
                    ).json()
                    if solution["request"] == "CAPCHA_NOT_READY":
                        print(solution["request"])
                        time.sleep(5)  # Increase sleep time to reduce request frequency
                    elif "ERROR" in solution["request"]:
                        print("Error:", solution["request"])
                        break
                    else:
                        print("Captcha Solved:", solution)
                        break

                # Use the received captcha response. Pass the answer to the configured callback function `cfCallback`

                await page.evaluate('cfCallback', solution["request"])

                await asyncio.sleep(15)

                await interactions(page)

            except Exception as e:
                print("An error occurred:", e)
                await browser.close()
        else:
            return


    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    async def interactions(page):
        try:
            await page.waitForSelector('#P500_FISCAL_YEAR_FROM', timeout=60000)
            await page.waitForSelector('#P500_FISCAL_YEAR_TO', timeout=60000)

            from_value = None
            to_value = None

            for attempt in range(3):
                try:
                    from_value = await page.evaluate('''() => {
                        const select = document.querySelector('#P500_FISCAL_YEAR_FROM');
                        return select ? select.options[select.options.length - 1].value : null;
                    }''')
                    if from_value:
                        await page.select('#P500_FISCAL_YEAR_FROM', from_value)
                        print(f"Selected last option in #P500_FISCAL_YEAR_FROM: {from_value}")
                        break
                except Exception as e:
                    print(f"Attempt {attempt + 1} failed for #P500_FISCAL_YEAR_FROM: {e}")
                    await asyncio.sleep(3)

            for attempt in range(3):
                try:
                    to_value = await page.evaluate('''() => {
                        const select = document.querySelector('#P500_FISCAL_YEAR_TO');
                        return select ? select.options[0].value : null;
                    }''')
                    if to_value:
                        await page.select('#P500_FISCAL_YEAR_TO', to_value)
                        print(f"Selected first option in #P500_FISCAL_YEAR_TO: {to_value}")
                        break
                except Exception as e:
                    print(f"Attempt {attempt + 1} failed for #P500_FISCAL_YEAR_TO: {e}")
                    await asyncio.sleep(3)

            if not from_value or not to_value:
                raise Exception("Unable to select fiscal year values after multiple attempts.")

            await page.waitForSelector('#B106150366531214971', timeout=60000)
            await page.click('#B106150366531214971')
            print("Submit button.")

            await asyncio.sleep(7)

            # await page.waitForSelector('#B47528447156014705', timeout=60000)
            # await page.click('#B47528447156014705')
            # print("Download button.")

            # Process only the first page
            await page.waitForSelector('#report_table_P510_RESULTS', timeout=80000)
            logger.info("Table loaded.")

            # Collect initial data into the modified array
            modified_array = []
            data_storage = []

            # Extract headers
            header_cells = await page.querySelectorAll('#report_table_P510_RESULTS thead th')
            headers = [await page.evaluate('(th) => th.innerText.trim()', th) for th in header_cells]

            # Build the modified array with initial data
            rows = await page.querySelectorAll('#report_table_P510_RESULTS tbody tr')
            for row in rows:
                cells = await row.querySelectorAll('td')
                cell_values = []
                for cell in cells:
                    cell_text = await page.evaluate('(cell) => cell.innerText.trim()', cell)
                    cell_values.append(cell_text)

                row_data = dict(zip(headers, cell_values))
                modified_array.append(row_data)

            # Process each row
            while modified_array:
                try:
                    current_row_data = modified_array[0]
                    contract_no = current_row_data.get('Contract No.')
                    amount = current_row_data.get('Amount')
                    logger.info(f"Processing Contract No.: {contract_no}, Amount: {amount}")

                    await page.waitForSelector('#report_table_P510_RESULTS', timeout=80000)
                    await asyncio.sleep(2)

                    # Re-fetch rows
                    rows = await page.querySelectorAll('#report_table_P510_RESULTS tbody tr')
                    row_found = False
                    for row in rows:
                        try:
                            cells = await row.querySelectorAll('td')
                            if len(cells) >= 5:
                                contractNoCell = cells[4]  # Adjust index if necessary
                                amountCell = cells[2]      # Adjust index if necessary
                                contractNoText = await page.evaluate('(cell) => cell.innerText.trim()', contractNoCell)
                                amountText = await page.evaluate('(cell) => cell.innerText.trim()', amountCell)
                                if contractNoText == contract_no and amountText == amount:
                                    await row.click()
                                    row_found = True
                                    logger.info("Clicked on row, waiting for details page to load...")
                                    break
                        except Exception as e:
                            logger.error(f"Error while checking row: {e}")
                            traceback.print_exc()

                    if not row_found:
                        logger.warning(f"Row with Contract No.: {contract_no} and Amount: {amount} not found.")
                        modified_array.pop(0)
                        continue

                    # Wait for the details page to load
                    try:
                        await page.waitForSelector('#P520_DESCRIPTION_CONTAINER', timeout=60000)
                        logger.info("Details page loaded.")
                    except Exception as e:
                        logger.error(f"Timeout waiting for details page for Contract No.: {contract_no}: {e}")
                        await page.goBack()
                        continue

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
                            title = await page.evaluate(f'''
                                () => {{
                                    const container = document.getElementById('{container_id}');
                                    if (container) {{
                                        const label = container.querySelector('label');
                                        return label ? label.innerText.trim() : null;
                                    }}
                                    return null;
                                }}
                            ''')
                            description = await page.evaluate(f'''
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
                            print(f'Could not extract details from container {container_id}: {e}')

                    # Store the extracted details
                    current_row_data['details'] = details
                    data_storage.append(current_row_data)
                    await asyncio.sleep(3)
                    # Click the back button to return to the table page
                    logger.info("Clicking 'Back' button to return to table page...")
                    await page.click('#B553373616548922883')

                    # Wait for the table to reappear
                    await page.waitForSelector('#report_table_P510_RESULTS', timeout=120000)
                    logger.info("Returned to table page.")

                    modified_array.pop(0)
                    await asyncio.sleep(2)

                except Exception as e:
                    logger.error(f"Exception during processing of Contract No.: {contract_no}: {e}")
                    traceback.print_exc()
                    modified_array.pop(0)
                    continue

            logger.info("Finished processing all rows on the first page.")
            logger.info("Extracted Table Data:")
            logger.info(json.dumps(data_storage, indent=2))

            await page.screenshot({'path': 'after_interaction.png'})

        except Exception as e:
            logger.error("An error occurred during interactions:", e)
            traceback.print_exc()

    page.on('console', lambda msg: asyncio.ensure_future(console_message_handler(msg)))

    await page.goto('https://service.yukon.ca/apps/contract-registry', waitUntil='networkidle2')

    await asyncio.sleep(45)

asyncio.get_event_loop().run_until_complete(main())