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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
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
                logger.info("Captcha enviado a 2Captcha")
                captcha_id = response.json()["request"]
                await asyncio.sleep(2)
                retries = 0
                max_retries = 10

                while retries < max_retries:
                    solution = requests.get(
                        f"https://2captcha.com/res.php?key={apikey}&action=get&json=1&id={captcha_id}"
                    ).json()
                    if solution["request"] == "CAPCHA_NOT_READY":
                        logger.debug("Captcha aún no está listo...")
                        await asyncio.sleep(5)
                        retries += 1
                    elif "ERROR" in solution["request"]:
                        logger.error("Error:", solution["request"])
                        break
                    else:
                        logger.info("Captcha resuelto exitosamente")
                        await page.evaluate('cfCallback', solution["request"])

                        captcha_solved_event.set()

                        if page_ready_event:
                            page_ready_event.set()
                        return
                else:
                    logger.warning("Failed to solve CAPTCHA after multiple attempts.")

                    stop_script_flag['stop'] = True

                    captcha_solved_event.set()
            except Exception as e:
                logger.error("An error occurred while solving Captcha:", e)

                stop_script_flag['stop'] = True
                captcha_solved_event.set()
        else:
            return

    page.on('console', lambda msg: asyncio.ensure_future(console_message_handler(msg)))

async def download_report(page, from_year, to_year):
    logger.info(f"Attempting to select fiscal years: From {from_year} to {to_year}")

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
    logger.info(f"Selected 'From' year: {from_year}")

    # Wait for the "To" options to update based on "From" selection
    await asyncio.sleep(10)  # Short delay to ensure options refresh

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
        logger.info(f"Selected 'To' year: {to_year}")
    else:
        logger.warning(f"'To' year {to_year} not available after selecting 'From' year {from_year}.")
        return False

    # Click the "Search" button
    search_button_selector = '#B106150366531214971'
    await page.waitForSelector(search_button_selector, {'timeout': 20000})
    await page.click(search_button_selector)
    logger.info("Botón de búsqueda presionado")
    
    # Wait for the download button to be available after results load
    download_button_selector = '#B47528447156014705'
    try:
        await page.waitForSelector(download_button_selector, {'timeout': 60000})
    except asyncio.TimeoutError:
        logger.warning("Timed out waiting for download button. Skipping this fiscal year range.")
        return False

    # Check for captcha again before clicking download
    captcha_present = await page.evaluate('''() => !!document.querySelector('#captcha-element-id')''')
    if captcha_present:
        logger.warning("CAPTCHA reapareció antes de descargar. Intentando resolver.")
        await setup_captcha_handling(page, captcha_solved_event, stop_script_flag)
        await captcha_solved_event.wait()
        captcha_solved_event.clear()
        await page.goto('https://service.yukon.ca/apps/contract-registry', {'waitUntil': 'networkidle2'})
        return False

    await asyncio.sleep(20)
    # Click the download button
    await page.click(download_button_selector)
    logger.info("Botón de descarga presionado")
    await asyncio.sleep(30)  # Adjust based on download size or implement a more reliable download completion check
    
    logger.info("Archivo descargado (se asume éxito)")
    
    return True

async def interactions_reports(page, captcha_solved_event, stop_script_flag, browser):
    fiscal_years = [
        "2007-08", "2008-09", "2009-10", "2010-11", "2011-12",
        "2013-14", "2014-15", "2015-16", "2016-17",
        "2017-18", "2018-19", "2019-20", "2020-21", "2021-22",
        "2022-23", "2023-24", "2024-25", "2025-26"
    ]

    for i in range(len(fiscal_years)):
        from_year = fiscal_years[i]
        to_year = fiscal_years[i]

        logger.info(f"Processing fiscal year range: From {from_year} to {to_year}")

        # Check if CAPTCHA is present and resolve if necessary
        captcha_present = await page.evaluate('''() => !!document.querySelector('#captcha-element-id')''')
        
        if captcha_present:
            logger.info("CAPTCHA detectado antes del intento de descarga. Resolviendo...")
            await setup_captcha_handling(page, captcha_solved_event, stop_script_flag)
            await captcha_solved_event.wait()
            captcha_solved_event.clear()
            await page.goto('https://service.yukon.ca/apps/contract-registry', {'waitUntil': 'networkidle2'})
            await asyncio.sleep(5)
            continue

        # Proceed with the report download if CAPTCHA is not stopping the script
        if stop_script_flag.get('stop'):
            logger.error("Script stopped due to CAPTCHA failure or other issue.")
            break

        # Attempt to download the report for the specified fiscal years
        success = await download_report(page, from_year, to_year)

        if not success:
            logger.warning(f"Skipping fiscal year range {from_year} to {to_year} due to an issue.")
            break

        # Go back to the main page after each download attempt
        await page.goto('https://service.yukon.ca/apps/contract-registry', {'waitUntil': 'networkidle2'})
        await asyncio.sleep(5)  # Adjust delay for page reload

    logger.info("Finished processing all fiscal years.")
     
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
        logger.error("Chrome executable not found. Please check your installation.")
        return

    logger.info(f"Using Chrome executable at: {chrome_path}")

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
    try:
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

        try:
            await wait_for_page_ready(page, page_ready_event)
        except Exception as e:
            logger.error(f"Error esperando que la página esté lista: {e}")
            await page.reload({'waitUntil': 'networkidle2'})
            await asyncio.sleep(5)

        # await interactions(page, captcha_solved_event, stop_script_flag, browser)
        await interactions_reports(page, captcha_solved_event, stop_script_flag, browser)
    
    finally:
        try:
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        except Exception as e:
            logger.warning(f"Error limpiando tareas pendientes: {e}")
        await browser.close()

async def wait_for_page_ready(page, page_ready_event):
    """Función para esperar que la página esté lista sin entrar en loop infinito."""
    try:
        # **Esperar que la página cargue completamente**
        await asyncio.wait_for(page_ready_event.wait(), timeout=10)
        logger.info("La página está lista.")
    except asyncio.TimeoutError:
        logger.warning("Tiempo de espera excedido para la página. Revisando manualmente...")

        # **Verifica si la página realmente está cargada**
        loaded = await page.evaluate('document.readyState')
        if loaded == "complete":
            logger.info("La página ya está completamente cargada.")
            page_ready_event.set()
        else:
            logger.warning("La página no ha terminado de cargar. Intentando recargar...")
            await page.reload({'waitUntil': 'networkidle2'})
            await asyncio.sleep(3)  # Esperar un poco después de recargar
            
if __name__ == '__main__':
    asyncio.run(main())
