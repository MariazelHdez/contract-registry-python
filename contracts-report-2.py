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

def save_progress(year, page, contract_no):
    progress = {'year': year, 'page': page, 'contract_no': contract_no}
    with open('progress.json', 'w') as f:
        json.dump(progress, f)

def load_progress():
    if os.path.exists('progress.json'):
        with open('progress.json', 'r') as f:
            return json.load(f)
    return {'year': None, 'page': 0, 'contract_no': None}

async def retry_action(action, retries=3, delay=5):
    for attempt in range(1, retries + 1):
        try:
            return await action()
        except Exception as e:
            print(f"Attempt {attempt} failed: {e}")
            if attempt < retries:
                await asyncio.sleep(delay)
            else:
                print("Max retries reached. Moving on.")
                return None
            
async def setup_captcha_handling(page, captcha_solved_event, stop_script_flag, page_ready_event=None):
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
            try:
                params = json.loads(txt.replace('intercepted-params:', ''))
                print("Intercepted Params:", params)
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
                response = requests.post("https://2captcha.com/in.php", data=payload).json()
                if response["status"] != 1:
                    #logger.error("Failed to submit CAPTCHA to 2Captcha.")
                    stop_script_flag['stop'] = True
                    captcha_solved_event.set()
                    return
                
                captcha_id = response["request"]
                logger.info("CAPTCHA submitted. Waiting for solution...")
                retries = 0
                max_retries = 10

                while retries < max_retries:
                    solution = requests.get(
                        f"https://2captcha.com/res.php?key={apikey}&action=get&json=1&id={captcha_id}"
                    ).json()
                    if solution["request"] == "CAPCHA_NOT_READY":
                        retries += 1
                        await asyncio.sleep(5)
                        continue
                    elif "ERROR" in solution["request"]:
                        logger.error(f"2Captcha Error: {solution['request']}")
                        stop_script_flag['stop'] = True
                        captcha_solved_event.set()
                        return
                    else:
                        logger.info("CAPTCHA solved successfully.")
                        await page.evaluate('cfCallback', solution["request"])
                        captcha_solved_event.set()
                        if page_ready_event:
                            page_ready_event.set()
                        return
                else:
                    logger.error("Failed to solve CAPTCHA after multiple attempts.")
                    stop_script_flag['stop'] = True
                    captcha_solved_event.set()
            except Exception as e:
                logger.error(f"Error handling CAPTCHA: {e}")
                stop_script_flag['stop'] = True
                captcha_solved_event.set()
        else:
            return

    page.on('console', lambda msg: asyncio.ensure_future(console_message_handler(msg)))

async def handle_captcha(page, captcha_solved_event, stop_script_flag, expected_element, page_ready_event=None):
    try:
        captcha_present = await page.evaluate('''() => !!document.querySelector('#captcha-element-id')''')
        if captcha_present:
            logger.info("CAPTCHA detected. Attempting to solve...")
            await setup_captcha_handling(page, captcha_solved_event, stop_script_flag, page_ready_event)
            await captcha_solved_event.wait()
            await page.waitForSelector(expected_element, {'timeout': 10000})
            captcha_solved_event.clear()
            logger.info("CAPTCHA solved successfully.")
    except Exception as e:
        logger.error(f"Error in handle_captcha: {e}")
        stop_script_flag['stop'] = True

async def select_years(page, from_year, to_year):
    try:
        print(f"Attempting to select fiscal years: From {from_year} to {to_year}")
        # Asegúrate de que el selector 'From' esté visible y listo para interactuar
        from_selector = '#P500_FISCAL_YEAR_FROM'
        await page.waitForXPath('//select[@id="P500_FISCAL_YEAR_FROM"]', {'timeout': 30000})    # Click and select "From" year
        
        # Cambia el valor del selector 'From'
        await page.evaluate(
            '''(year) => {
                const fromSelect = document.querySelector('#P500_FISCAL_YEAR_FROM');
                fromSelect.value = year;
                fromSelect.dispatchEvent(new Event('change', { bubbles: true }));
            }''',
            from_year
        )
        print(f"Selected 'From' year: {from_year}")
        
        # Espera un momento para que las opciones 'To' se actualicen dinámicamente
        await asyncio.sleep(2)
        
        # Asegúrate de que el selector 'To' esté visible y listo
        to_selector = '#P500_FISCAL_YEAR_TO'
        await page.waitForSelector(to_selector, {'timeout': 30000, 'visible': True})
        
        # Verifica que el año 'To' esté disponible en las opciones
        to_options = await page.evaluate(
            '''() => {
                const toSelect = document.querySelector('#P500_FISCAL_YEAR_TO');
                return Array.from(toSelect.options).map(option => option.value);
            }'''
        )
        
        if to_year in to_options:
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
        
        # Haz clic en el botón de búsqueda
        search_button_selector = '#B106150366531214971'
        await page.waitForSelector(search_button_selector, {'timeout': 20000, 'visible': True})
        await page.click(search_button_selector)
        print("Search button clicked.")
        return True

    except Exception as e:
        print(f"Error selecting years {from_year} to {to_year}: {e}")
        traceback.print_exc()
        return False

    # 
    # await page.click('#P500_FISCAL_YEAR_FROM')
    # await page.evaluate(
    #     '''(year) => {
    #         const fromSelect = document.querySelector('#P500_FISCAL_YEAR_FROM');
    #         fromSelect.value = year;
    #         fromSelect.dispatchEvent(new Event('change', { bubbles: true }));
    #     }''',
    #     from_year
    # )
    # print(f"Selected 'From' year: {from_year}")

    # # Wait for the "To" options to update based on "From" selection
    # await asyncio.sleep(2)  # Short delay to ensure options refresh

    # # Click and select "To" year after verifying it's available
    # to_options = await page.Jeval('#P500_FISCAL_YEAR_TO', '(el) => Array.from(el.options).map(o => o.value)')
    # if to_year in to_options:
    #     await page.click('#P500_FISCAL_YEAR_TO')
    #     await page.evaluate(
    #         '''(year) => {
    #             const toSelect = document.querySelector('#P500_FISCAL_YEAR_TO');
    #             toSelect.value = year;
    #             toSelect.dispatchEvent(new Event('change', { bubbles: true }));
    #         }''',
    #         to_year
    #     )
    #     print(f"Selected 'To' year: {to_year}")
    # else:
    #     print(f"'To' year {to_year} not available after selecting 'From' year {from_year}.")
    #     return False

    # Click the "Search" button
    # search_button_selector = '#B106150366531214971'
    # await page.waitForSelector(search_button_selector, {'timeout': 20000})
    
async def interactions_reports(page, captcha_solved_event, stop_script_flag, browser):
    fiscal_years = [
        "2007-08", "2008-09", "2009-10", "2010-11", "2011-12",
        "2012-13", "2013-14", "2014-15", "2015-16", "2016-17",
        "2017-18", "2018-19", "2019-20", "2020-21", "2021-22",
        "2022-23", "2023-24", "2024-25"
    ]

    def resume_fiscal_years(fiscal_years, from_year):
        if from_year in fiscal_years:
            start_index = fiscal_years.index(from_year)
            return fiscal_years[start_index:]
        else:
            print(f"Warning: {from_year} not found in fiscal_years. Starting from the beginning.")
            return fiscal_years
    progress = load_progress()
    if progress['year']:
        print(f"Resuming from year: {progress['year']}, page: {progress['page']}, contract: {progress['contract_no']}")
        # Recortar la lista de años
        fiscal_years = resume_fiscal_years(fiscal_years, progress['year'])
    else:
        print("No saved progress found. Starting from the beginning.")

    print("Fiscal years to process:", fiscal_years)


    progress = load_progress()
    if progress['year']:
        print(f"Resuming from year: {progress['year']}, page: {progress['page']}, contract: {progress['contract_no']}")

    #print("111111.")
    # await page.waitForFunction('document.readyState === "complete"', {'timeout': 40000})
    # print("222222.")
    for i in range(len(fiscal_years) - 1):
        # Proceed with the report download if CAPTCHA is not stopping the script
        if stop_script_flag.get('stop'):
            print("Script stopped due to CAPTCHA failure or other issue.")
            break
        from_year = fiscal_years[i]
        to_year = fiscal_years[i + 1]
        try:
            print(f"Processing fiscal year range: From {from_year} to {to_year}")
            await handle_captcha(page, captcha_solved_event, stop_script_flag, '#P500_FISCAL_YEAR_TO')
            if stop_script_flag.get('stop'):
                print("Script stopped due to CAPTCHA failure or other issue.")
                break
            #await page.waitForFunction('document.readyState === "complete"', {'timeout': 20000})
            await page.waitForXPath('//select[@id="P500_FISCAL_YEAR_FROM"]', {'timeout': 30000})    # Click and select "From" year
            await select_years(page, from_year, to_year)
            await asyncio.sleep(4)
            await interactions(page, captcha_solved_event, stop_script_flag, browser, from_year, to_year)
        except Exception as e:
            print(f"Error processing fiscal year: From {from_year} to {to_year}")
            traceback.print_exc()
        try:  
            # Go back to the main page after each download attempt
            await page.goto('https://service.yukon.ca/apps/contract-registry', {'waitUntil': 'networkidle2'})
            await handle_captcha(page, captcha_solved_event, stop_script_flag, '#P500_FISCAL_YEAR_TO')

            await page.waitForSelector('#P500_FISCAL_YEAR_TO', timeout=10000)
        except Exception as e:
            print(f"Failed to reload main page for next fiscal year: {e}")
            break
    print("Finished processing all fiscal years.")

async def save_incremental_data(data_storage, filename):
    try:
        existing_data = []
        if os.path.exists(filename) and os.path.getsize(filename) > 0:
            with open(filename, 'r') as f:
                try:
                    existing_data = json.load(f)
                except json.JSONDecodeError:
                    logger.warning(f"El archivo {filename} no contiene JSON válido. Sobrescribiendo con nuevos datos.")

        # Asegúrate de que los datos existentes y nuevos sean listas
        if not isinstance(existing_data, list):
            logger.warning(f"El archivo {filename} no contiene una lista. Sobrescribiendo con nuevos datos.")
            existing_data = []

        # Verifica que los datos nuevos sean una lista
        if not isinstance(data_storage, list):
            logger.error("Los datos proporcionados no son una lista. No se guardarán.")
            return

        # Concatenar nuevos datos con los existentes
        existing_data.extend(data_storage)
        logger.error("Se contactenan.")

        # Guardar en el archivo
        with open(filename, 'w') as f:
            json.dump(existing_data, f, indent=2)

        logger.info(f"Data concatenated and saved to {filename}.")
    except Exception as e:
        logger.error(f"Error saving incremental data to disk: {e}")

async def interactions(page, captcha_solved_event, stop_script_flag, browser, fiscal_year_from, fiscal_year_to):
    try:
        data_storage = []
        invalid_contracts = []
        page_counter = 0  # Counter for processed pages
        cpu_count = os.cpu_count() or 5 
        logger.info(f"CONTADOR {cpu_count}.")
        # Limit the number of concurrent tasks
        semaphore = asyncio.Semaphore(cpu_count)  # Adjusted the number to reduce concurrency
        filename = f"{fiscal_year_from}_{fiscal_year_to}_data.json"
        invalid_filename = f"{fiscal_year_from}_{fiscal_year_to}_invalid_contracts.json"
        while not stop_script_flag.get('stop'):
            try:
                table_selector = '#report_table_P510_RESULTS'
                submit_button_selector = '#B106150366531214971'
                table_exists = await page.querySelector(table_selector)
                if not table_exists:
                    submit_button = await page.querySelector(submit_button_selector)
                    if submit_button:
                        print("Submit button found. Clicking to load table.")
                        await page.waitForSelector(submit_button_selector, timeout=60000)
                        await submit_button.click()
                        await page.waitForSelector(table_selector, timeout=80000)
                        print("Table loaded successfully.")
                    else:
                        print("Neither table nor submit button found. Skipping this fiscal year.")
                        break
                else:
                    print("Table already loaded.")

                while True:
                    print(f"111111")
                    header_cells = await page.querySelectorAll('#report_table_P510_RESULTS thead th')
                    header_ids = []
                    for header_cell in header_cells:
                        header_id = await page.evaluate('(cell) => cell.getAttribute("id")', header_cell)
                        header_ids.append(header_id)
                    row_data_list = []
                    rows = await page.querySelectorAll('#report_table_P510_RESULTS tbody tr')
                    print(f"222222")
                    for row in rows:
                        cells = await row.querySelectorAll('td')
                        row_data = {}
                        detail_url = None
                        for index, cell in enumerate(cells):
                            # Use header IDs for the row_data keys
                            header_id = header_ids[index] if index < len(header_ids) else f'Column_{index}'
                            cell_text = await page.evaluate('(cell) => cell.innerText.trim()', cell)
                            row_data[header_id] = cell_text

                            # Check for detail link in the cell
                            if not detail_url:
                                link_element = await cell.querySelector('a')
                                if link_element:
                                    href = await page.evaluate('(a) => a.getAttribute("href")', link_element)
                                    detail_url = urljoin(page.url, href)
                        row_data['detail_url'] = detail_url
                        row_data_list.append(row_data)
                        save_progress(fiscal_year_from, page_counter, row_data['Contract Number'])

                    tasks = [
                        asyncio.create_task(process_row(row, browser, stop_script_flag, data_storage, invalid_contracts, semaphore))
                        for row in row_data_list
                    ]
                    print(f"33333")
                    await asyncio.gather(*tasks)
                    print(f"Completed processing page {page_counter + 1}.")

                    page_counter += 1
                    await save_incremental_data(data_storage, filename)
                    await save_incremental_data(invalid_contracts, invalid_filename)
                    data_storage.clear()
                    invalid_contracts.clear()
                    next_button_selector = '.t-Report-paginationLink--next'
                    next_button = await page.querySelector(next_button_selector)
                    if next_button:
                        try:
                            await next_button.click()
                            print("Navigating to next page...")
                            await page.waitForNavigation({'waitUntil': 'networkidle2'})
                        except TimeoutError:
                            print("Timeout while waiting for next page to load.")
                            break
                    else:
                        print("No more pages to process.")
                        break
            except Exception as e:
                print(f"Error processing page {page_counter + 1}: {e}")
                traceback.print_exc()
                break
    except Exception as e:
        print(f"An error occurred during interactions for {fiscal_year_from}-{fiscal_year_to}: {e}")
        traceback.print_exc()

async def process_row(row_data, browser, stop_script_flag, data_storage, invalid_contracts, semaphore):
    detail_page = None  # Asegúrate de inicializar detail_page
    async with semaphore:
        # if stop_script_flag.get('stop'):
        #     logger.info("Stopping script due to CAPTCHA failure.")
        #     return
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
            await handle_captcha(detail_page, detail_captcha_solved_event, stop_script_flag,"#P520_DESCRIPTION_LABEL" )
            # await setup_captcha_handling(detail_page, detail_captcha_solved_event)

            max_retries = 5
            retries = 0
            while retries < max_retries:
                try:
                    # response = await detail_page.goto(detail_url, {'waitUntil': 'networkidle2', 'timeout': 60000})
                    response = await retry_action(lambda: detail_page.goto(detail_url, {'waitUntil': 'networkidle2', 'timeout': 60000}))
                    if not response:
                        logger.error(f"Failed to load page after retries: {detail_url}")
                        return
                    logger.info(f"Loaded detail page for Contract No.: {contract_no}")
                    detail_captcha_solved_event = asyncio.Event()
                    await handle_captcha(detail_page, detail_captcha_solved_event, stop_script_flag,"#P520_DESCRIPTION_LABEL")
                except TimeoutError:
                    logger.error(f"Timeout while loading detail page for Contract No.: {contract_no}")
                    retries += 1
                    continue
                # Get page content to check for CAPTCHA indicators
                page_content = await detail_page.content()

                if response.status == 403:
                    logger.error(f"Server-side 403 error on detail page for Contract No.: {contract_no}. Skipping this contract.")
                    # Append the contract number to invalid_contracts
                    invalid_contracts.append(contract_no)

                    await detail_page.close()
                    return
                elif response.status == 504:
                    logger.warning(f"504 Gateway Timeout for Contract No.: {contract_no}. Retrying...")
                    retries += 1
                    await asyncio.sleep(5)  
                    try:
                        await detail_page.reload({'waitUntil': 'networkidle2', 'timeout': 60000})
                        logger.info(f"Reloaded page for Contract No.: {contract_no}")
                    except TimeoutError:
                        logger.error(f"Timeout while reloading page for Contract No.: {contract_no}. Retrying...")
                    continue  # Retry the navigation
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
            details = await extract_details(detail_page)
            row_data.update(details)
            data_storage.append(row_data)
            logger.info(f"Processed and stored details for Contract No.: {contract_no}")
        except Exception as e:
            logger.error(f"Exception during processing of Contract No.: {contract_no}: {e}")
            traceback.print_exc()
            await detail_page.close()
            return
        finally:
            if detail_page:
                await detail_page.close()

async def extract_details(detail_page):
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
        ('P520_SOA_NUMBER_LABEL', 'P520_SOA_NUMBER'),
    ]
    for label, field in detail_fields:
        try:
            value = await detail_page.evaluate(f'''
                () => {{
                    const element = document.querySelector('#{field}');
                    return element ? element.innerText.trim() : null;
                }}
            ''')
            if value:
                details[label] = value
        except Exception as e:
            logger.error(f"Error extracting {label}: {e}")
    return details

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

    # Go to the initial page
    await page.goto('https://service.yukon.ca/apps/contract-registry', waitUntil='networkidle2')

   # await handle_captcha(page, captcha_solved_event, stop_script_flag, '#P500_FISCAL_YEAR_FROM',page_ready_event)
  
    # Solve captcha again
    try:
        await handle_captcha(page, captcha_solved_event, stop_script_flag, '#P500_FISCAL_YEAR_FROM', page_ready_event)
        await asyncio.sleep(5)
    except asyncio.TimeoutError:
        logger.error("Timed out waiting for the page to be ready. Retrying...")
        await page.reload({'waitUntil': 'networkidle2'})
    await interactions_reports(page, captcha_solved_event, stop_script_flag,browser)
    await browser.close()

if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
