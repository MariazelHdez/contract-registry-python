import argparse
import asyncio
import os
import json
import logging
import psycopg2
import psycopg2.extras
import requests
from pyppeteer import launch
from pyppeteer_stealth import stealth

from utils import find_chrome_executable, USER_AGENT
# CONFIG
API_KEY = "2c33ca4e0cc4ad9ec06f50e8c4a3eea9"  
YUKON_URL = 'https://service.yukon.ca/apps/contract-registry'
PROGRESS_FILE = "contract_details.jsonl"
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", 5))


# DB config desde entorno
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'dbname': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'port': os.getenv('DB_PORT', 5432),
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_contract_numbers():
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT contract_no FROM contracts")
            return [row[0] for row in cur.fetchall()]


def load_processed_contracts():
    if not os.path.exists(PROGRESS_FILE):
        return set()
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        return set(json.loads(line)["contract_no"] for line in f if line.strip())


def insert_details_into_db(jsonl_path: str) -> None:
    """Insert contract details stored in a JSONL file into PostgreSQL."""
    if not os.path.exists(jsonl_path):
        logger.warning("Progress file not found, nothing to insert")
        return

    records = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    if not records:
        logger.info("No records to insert into database")
        return

    columns = [
        "contract_no",
        "p520_description",
        "p520_department",
        "p520_project_manager",
        "p520_work_community",
        "p520_postal_code",
        "p520_yukon_business",
        "p520_yfn_business",
        "p520_contract_type",
        "p520_tender_type",
        "p520_tender_class",
        "p520_soa_number",
    ]

    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS contract_details (
                    contract_no TEXT PRIMARY KEY,
                    p520_description TEXT,
                    p520_department TEXT,
                    p520_project_manager TEXT,
                    p520_work_community TEXT,
                    p520_postal_code TEXT,
                    p520_yukon_business TEXT,
                    p520_yfn_business TEXT,
                    p520_contract_type TEXT,
                    p520_tender_type TEXT,
                    p520_tender_class TEXT,
                    p520_soa_number TEXT
                )
                """
            )

            insert_query = (
                "INSERT INTO contract_details (" + ",".join(columns) + ") "
                "VALUES (" + ",".join(["%s"] * len(columns)) + ") "
                "ON CONFLICT (contract_no) DO NOTHING"
            )

            values = []
            for record in records:
                row = [record.get(col) for col in columns]
                values.append(row)

            psycopg2.extras.execute_batch(cur, insert_query, values)
        conn.commit()

    logger.info("Inserted %d records into contract_details", len(values))


async def setup_captcha(page):
    await page.evaluateOnNewDocument("""
        () => {
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
    """)

    async def on_console(msg):
        if 'intercepted-params:' in msg.text:
            params = json.loads(msg.text.split('intercepted-params:')[1])
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
            r = requests.post("https://2captcha.com/in.php", data=payload).json()
            captcha_id = r["request"]

            for _ in range(20):
                await asyncio.sleep(5)
                res = requests.get(
                    f"https://2captcha.com/res.php?key={API_KEY}&action=get&json=1&id={captcha_id}"
                ).json()
                if res["request"] == "CAPCHA_NOT_READY":
                    continue
                elif "ERROR" in res["request"]:
                    logger.error(f"Captcha error: {res['request']}")
                    return
                else:
                    await page.evaluate('cfCallback', res["request"])
                    logger.info("Captcha resuelto")
                    return

    page.on('console', lambda msg: asyncio.ensure_future(on_console(msg)))


async def extract_contract_details(page, contract_no):
    """Navigate to the contract registry and extract details for a contract."""
    await page.goto(YUKON_URL, {'waitUntil': 'networkidle2'})
    await page.waitForSelector('#P500_KEYWORD')

    # Select fiscal year range
    await page.click('#P500_FISCAL_YEAR_FROM')
    await page.evaluate(
        '''(year) => {
            const fromSelect = document.querySelector('#P500_FISCAL_YEAR_FROM');
            fromSelect.value = year;
            fromSelect.dispatchEvent(new Event('change', { bubbles: true }));
        }''',
        "2007-08"
    )

    # Wait for the "To" options to refresh before selecting 2025-26
    await asyncio.sleep(2)
    to_options = await page.Jeval('#P500_FISCAL_YEAR_TO', '(el) => Array.from(el.options).map(o => o.value)')
    if "2025-26" in to_options:
        await page.click('#P500_FISCAL_YEAR_TO')
        await page.evaluate(
            '''(year) => {
                const toSelect = document.querySelector('#P500_FISCAL_YEAR_TO');
                toSelect.value = year;
                toSelect.dispatchEvent(new Event('change', { bubbles: true }));
            }''',
            "2025-26"
        )
    else:
        logger.warning("Fiscal year 2025-26 not available after selecting 2007-08")

    await asyncio.sleep(1)

    # Search for the contract
    await page.evaluate('document.querySelector("#P500_KEYWORD").value = ""')
    await page.type('#P500_KEYWORD', contract_no)
    search_button_selector = '#B106150366531214971'
    await page.waitForSelector(search_button_selector, {'timeout': 20000})
    await asyncio.gather(
        page.waitForNavigation({'waitUntil': 'networkidle2'}),
        page.click(search_button_selector),
    )
    logger.info("Botón de búsqueda presionado")

    row_selector = 'table.t-Report-report tbody tr'

    await page.waitForSelector(row_selector, timeout=60000)
    await asyncio.sleep(2)

    table_selector = '#report_P510_RESULTS'
    await asyncio.sleep(7)  # Short delay to ensure options refresh

    await page.waitForSelector(table_selector, timeout=60000)
    await asyncio.sleep(5)  # Short delay to ensure options refresh

    logger.info("Se termina la espera")

    rows = await page.querySelectorAll(row_selector)
    target_row = None
    for row in rows:
        cell = await row.querySelector('td[headers="Contract Number"]')
        if cell:
            text = await page.evaluate('(el) => el.innerText.trim()', cell)
            if text == contract_no:
                target_row = row
                break

    if not target_row:
        logger.warning(f"Exact row for {contract_no} not found, using first row")
        target_row = rows[0] if rows else None

    if not target_row:
        raise Exception(f"No results found for {contract_no}")

    await target_row.click()
    await page.waitForNavigation({'waitUntil': 'networkidle2'})

   

    await page.waitForSelector('#P520_DESCRIPTION_CONTAINER', timeout=60000)
    logger.info("Encontro el container")
    await asyncio.sleep(5)  # Short delay to ensure options refresh

    # Campos deseados
    field_ids = [
        'P520_DESCRIPTION', 'P520_DEPARTMENT', 'P520_PROJECT_MANAGER',
        'P520_WORK_COMMUNITY', 'P520_POSTAL_CODE', 'P520_YUKON_BUSINESS',
        'P520_YFN_BUSINESS', 'P520_CONTRACT_TYPE', 'P520_TENDER_TYPE',
        'P520_TENDER_CLASS', 'P520_SOA_NUMBER'
    ]

    detail = {'contract_no': contract_no}
    for field in field_ids:
        try:
            selector = f'#{field}_CONTAINER span'
            text = await page.evaluate(f'document.querySelector("{selector}")?.innerText.trim()')
            detail[field.lower()] = text
        except Exception as e:
            logger.warning(f"No se pudo extraer {field}: {e}")
    logger.warning(f"Se extrajo")

    return detail


async def main(headless: bool = False):
    contract_numbers = get_contract_numbers()
    processed = load_processed_contracts()
    chrome_path = find_chrome_executable()
    if not chrome_path:
        logger.error("Chrome executable not found. Please check your installation.")
        return

    logger.info(f"Using Chrome executable at: {chrome_path}")

    browser = await launch(
        executablePath=chrome_path,
        headless=headless,
        devtools=False,
        autoClose=False,
        args=[
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-blink-features=AutomationControlled',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
        ]
    )

    file_lock = asyncio.Lock()

    async def process(contract_no, semaphore):
        if contract_no in processed:
            return
        async with semaphore:
            page = await browser.newPage()
            await page.setUserAgent(USER_AGENT)
            await stealth(page)
            await setup_captcha(page)
            try:
                logger.info(f"Procesando contrato: {contract_no}")
                detail = await extract_contract_details(page, contract_no)
                async with file_lock:
                    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
                        f.write(json.dumps(detail) + "\n")
            except Exception as e:
                logger.error(f"Error procesando {contract_no}: {e}")
            finally:
                await page.close()

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = [asyncio.create_task(process(cn, semaphore)) for cn in contract_numbers]
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await browser.close()

    insert_details_into_db(PROGRESS_FILE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract contract details")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome in headless mode to avoid opening a browser window",
    )
    args = parser.parse_args()
    asyncio.run(main(headless=args.headless))
