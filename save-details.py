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
import csv
import psycopg2
import os
import glob
from dotenv import load_dotenv
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Set environment variable to skip Chromium download
os.environ['PYPPETEER_SKIP_CHROMIUM_DOWNLOAD'] = 'True'

API_KEY = "2c33ca4e0cc4ad9ec06f50e8c4a3eea9"  # Replace with your actual 2Captcha API key

# Cargar variables de entorno
load_dotenv()

# Acceder a las credenciales de la base de datos
db_user = os.getenv('DB_USER')
db_password = os.getenv('DB_PASSWORD')
db_host = os.getenv('DB_HOST')
db_name = os.getenv('DB_NAME')
db_port = os.getenv('DB_PORT')

# Conectar a la base de datos PostgreSQL
conn = psycopg2.connect(
    host=db_host,  
    database=db_name,
    user=db_user,
    password=db_password, 
    port=db_port
)

# Crear cursor para ejecutar consultas SQL
cur = conn.cursor()

GET_CONTRACTS_NO = """
    SELECT contract_no
    FROM contracts
    GROUP BY contract_no
    ORDER BY COUNT(contract_no);
"""



async def main():
    chrome_path = find_chrome_executable()
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

    # Setup CAPTCHA handling
    await setup_captcha_handling(page, captcha_solved_event, stop_script_flag, page_ready_event)

    # Visit the target page
    await page.goto('https://service.yukon.ca/apps/contract-registry', waitUntil='networkidle2')

    # Solve CAPTCHA if required
    await solve_captcha_if_needed(page, captcha_solved_event, page_ready_event, stop_script_flag)
    contracts = await get_contract_numbers()
    # Proceed with further interactions
    await search_contracts(contracts, page,browser)

    await browser.close()


async def extract_contract_details(page, browser, stop_script_flag, contract_no):
    """Extracts contract details for a given contract number."""
    logger.info(f"Extracting details for Contract No.: {contract_no}")

    # Wait for the results table to appear
    table_selector = '#report_table_P510_RESULTS'
    await page.waitForSelector(table_selector, timeout=10000)

    # Get all table rows
    rows = await page.querySelectorAll('#report_table_P510_RESULTS tbody tr')
    data_storage = []

    for row in rows:
        # Get all table cells in the row
        cells = await row.querySelectorAll('td')
        row_data = {}
        detail_url = None

        # Extract data from each column
        for index, cell in enumerate(cells):
            cell_text = await page.evaluate('(cell) => cell.innerText.trim()', cell)
            row_data[f"column_{index}"] = cell_text

            # Extract the detail link
            link_element = await cell.querySelector('a')
            if link_element and not detail_url:
                href = await page.evaluate('(a) => a.getAttribute("href")', link_element)
                detail_url = urljoin(page.url, href)

        # If no detail URL, skip this row
        if not detail_url:
            logger.warning(f"No detail URL found for Contract No.: {contract_no}, skipping.")
            continue

        # Process the details page
        row_data.update(await fetch_contract_detail(detail_url, browser, stop_script_flag))

        # Store the extracted data
        data_storage.append(row_data)

    return data_storage

async def fetch_contract_detail(detail_url, browser, stop_script_flag):
    """Visits the contract detail page, solves CAPTCHA if needed, and extracts contract details."""
    detail_page = await browser.newPage()

    try:
        await detail_page.goto(detail_url, waitUntil="networkidle2", timeout=20000)

        # Solve CAPTCHA if needed
        captcha_solved_event = asyncio.Event()
        page_ready_event = asyncio.Event()

        await setup_captcha_handling(detail_page, captcha_solved_event, stop_script_flag, page_ready_event)
        if not await solve_captcha_if_needed(detail_page, captcha_solved_event, page_ready_event, stop_script_flag):
            logger.error(f"CAPTCHA could not be solved for {detail_url}, skipping.")
            return {}

        # Wait for contract detail page to load
        await detail_page.waitForSelector('#P520_DESCRIPTION_CONTAINER', timeout=20000)

        # Extract contract details
        details = {}
        fields = [
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

        for label_id, value_id in fields:
            try:
                label = await detail_page.evaluate(f'document.getElementById("{label_id}").innerText.trim()')
                value = await detail_page.evaluate(f'document.getElementById("{value_id}").innerText.trim()')
                details[label] = value
            except:
                continue

        logger.info(f"Extracted contract details: {details}")
        await detail_page.close()
        return details

    except Exception as e:
        logger.error(f"Error while processing contract details from {detail_url}: {e}")
        traceback.print_exc()
        await detail_page.close()
        return {}

async def save_data_to_json(data_storage, filename="contract_data.json"):
    """Appends extracted contract details to an existing JSON file instead of overwriting."""
    try:
        # Try to read existing data
        try:
            with open(filename, 'r') as f:
                existing_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            existing_data = []

        # Merge new data with existing data
        updated_data = existing_data + data_storage

        # Write back to the file
        with open(filename, 'w') as f:
            json.dump(updated_data, f, indent=2)

        logger.info(f"Data appended to {filename}")
    except Exception as e:
        logger.error(f"Error saving data to JSON: {e}")

async def search_contracts(contract_numbers,page, browser):
    """Searches contract numbers, extracts details, and saves data to JSON."""

    all_data = []

    for contract_no in contract_numbers:
        print(f"Searching for contract: {contract_no}")

        await page.goto("https://service.yukon.ca/apps/contract-registry", waitUntil="networkidle2", timeout=20000)

        # Solve CAPTCHA if needed
        captcha_solved_event = asyncio.Event()
        page_ready_event = asyncio.Event()
        stop_script_flag = {"stop": False}

        # await setup_captcha_handling(page, captcha_solved_event, stop_script_flag, page_ready_event)
        # if not await solve_captcha_if_needed(page, captcha_solved_event, page_ready_event, stop_script_flag):
        #     logger.error(f"CAPTCHA could not be solved for contract {contract_no}, skipping.")
        #     continue
        elementFrom =   await page.waitForXPath('//select[@id="P500_FISCAL_YEAR_FROM"]', {'timeout': 30000})
        if elementFrom:
            print("El selector existe en la página.")
        else:
            print("El selector NO existe.")
        await page.evaluate(
            '''(year) => {
                const fromSelect = document.querySelector('#P500_FISCAL_YEAR_FROM');
                fromSelect.value = year;
                fromSelect.dispatchEvent(new Event('change', { bubbles: true }));
            }''',
            "2007-08"
        )
        await asyncio.sleep(2)

        # Fill the input field with contract number
        element = await page.querySelector('#P500_KEYWORD')
        if element:
            print("El selector existe en la página.")
        else:
            print("El selector NO existe.")
        
        await page.type('#P500_KEYWORD', contract_no, force=True)
        # await page.waitForSelector('#P500_KEYWORD', timeout=10000)
        # await page.type('#P500_KEYWORD', contract_no)

        # Click the search button
        submit_button_selector = '#B106150366531214971'
        submit_button = await page.querySelector(submit_button_selector)
        if submit_button is not None:
            await page.waitForSelector(submit_button_selector, timeout=60000)
            await page.click(submit_button_selector)
            print("Submit button clicked.")

        # Wait for results to load
        await asyncio.sleep(3)

        # Extract contract details from search results
        data = await extract_contract_details(page, browser, stop_script_flag, contract_no)
        all_data.extend(data)

    await save_data_to_json(all_data)



async def get_contract_numbers():
    """Fetches contract numbers from PostgreSQL and returns them as a list."""
    try:
        cur.execute(GET_CONTRACTS_NO)
        contract_numbers = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        return contract_numbers
    except Exception as e:
        print(f"Error retrieving contract numbers: {e}")
        return []

async def solve_captcha_if_needed(page, captcha_solved_event, page_ready_event, stop_script_flag):
    """Checks for CAPTCHA and solves it if required."""
    try:
        # Wait for the page to load
        await asyncio.wait_for(page_ready_event.wait(), timeout=10)
    except asyncio.TimeoutError:
        logger.error("Timed out waiting for the page to be ready. Retrying...")
        await page.reload({'waitUntil': 'networkidle2'})

    captcha_present = await page.evaluate('''() => !!document.querySelector('#captcha-element-id')''')
    if captcha_present:
        logger.info("CAPTCHA detected, solving...")
        await captcha_solved_event.wait()
        return False
    else:
        logger.info("No CAPTCHA detected, proceeding.")
        return False


async def setup_captcha_handling(page, captcha_solved_event, stop_script_flag, page_ready_event=None):
    """Sets up CAPTCHA interception and solving logic."""
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

    async def console_message_handler(msg):
        txt = msg.text
        if 'intercepted-params:' in txt:
            params = json.loads(txt.replace('intercepted-params:', ''))
            print("Intercepted Params:", params)
            await solve_captcha(page, params, captcha_solved_event, stop_script_flag, page_ready_event)

    page.on('console', lambda msg: asyncio.ensure_future(console_message_handler(msg)))


async def solve_captcha(page, params, captcha_solved_event, stop_script_flag, page_ready_event):
    """Handles CAPTCHA solving via 2Captcha."""
    try:
        payload = {
            "key": API_KEY,
            "method": "turnstile",
            "sitekey": params["sitekey"],
            "pageurl": params["pageurl"],
            "data": params["data"],
            "pagedata": params["pagedata"],
            "action": params["action"],
            "useragent": params["userAgent"],
            "json": 1,
        }

        response = requests.post("https://2captcha.com/in.php", data=payload)
        captcha_id = response.json()["request"]

        retries = 0
        max_retries = 10
        while retries < max_retries:
            solution = requests.get(
                f"https://2captcha.com/res.php?key={API_KEY}&action=get&json=1&id={captcha_id}"
            ).json()
            if solution["request"] == "CAPCHA_NOT_READY":
                print("Captcha not ready, waiting...")
                await asyncio.sleep(5)
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
        print("An error occurred while solving CAPTCHA:", e)
        stop_script_flag['stop'] = True
        captcha_solved_event.set()


def find_chrome_executable():
    """Finds the path to Chrome executable on different OS."""
    possible_paths = [
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
        '/usr/bin/google-chrome',
        '/usr/local/bin/google-chrome',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ]

    for path in possible_paths:
        if os.path.exists(path):
            return path

    return None


if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())