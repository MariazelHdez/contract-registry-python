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


async def reserve_clase(class_name, page):
    try:
        await page.waitForSelector('.card.clase', {'timeout': 10000})
        class_cards = await page.querySelectorAll('.card.clase')

        for card in class_cards:
            text_content = await page.evaluate('(el) => el.innerText', card)
            if class_name in text_content:
                await card.click()
                logger.info("Clase '" + class_name + "' seleccionada.")
                break
        else:
            logger.warning("No se encontró la clase '" + class_name + "'.")
    except Exception as e:
        logger.error(f"Error buscando o haciendo clic en la clase: {e}")

    logger.info("Esperando que se abra el modal de la clase...")
    await asyncio.sleep(10)
    try:
        await page.waitForSelector('.ant-modal-content', {'timeout': 10000})
        modal_title = await page.querySelector('.ant-modal-content .nombre')
        title_text = await page.evaluate('(el) => el.innerText', modal_title)

        if class_name in title_text:
            logger.info("Título del modal confirmado. Procediendo a reservar...")
            try:
                modal_text = await page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('.ant-modal-content span')).map(el => el.innerText.toLowerCase());
                }''')
                if any("reserved" in t for t in modal_text) or any("cancel booking" in t for t in modal_text):
                    logger.info(f"La clase '{class_name}' ya está reservada (detectado por contenido del modal).")
                    await page.click('.ant-modal-close')
                    return
                if any("no places left" in t for t in modal_text):
                    logger.warning(f"No hay cupos disponibles para la clase '{class_name}'.")
                    await page.click('.ant-modal-close')
                    return
            except Exception as e:
                logger.warning(f"No se pudo verificar estado del modal: {e}")
            # Revisar si ya está reservado (detección más flexible por texto)
            # try:
            #     reserved = await page.querySelector('.ant-modal-content span')
            #     if reserved:
            #         reserved_text = await page.evaluate('(el) => el.innerText', reserved)
            #         if "reserved" in reserved_text.lower():
            #             logger.info(f"La clase '{class_name}' ya está reservada (detectado por texto).")
            #             await page.click('.ant-modal-close')
            #             return
            # except Exception as e:
            #     logger.warning(f"No se pudo verificar si ya está reservado: {e}")

            # Revisar si aparece "No places left"
            # try:
            #     no_places = await page.querySelector('.botonReservar.ant-btn-primary span')
            #     if no_places:
            #         no_places_text = await page.evaluate('(el) => el.innerText', no_places)
            #         if "no places left" in no_places_text.lower():
            #             logger.warning("No hay cupos disponibles para esta clase.")
            #             await page.click('.ant-modal-close')
            #             return
            # except Exception as e:
            #     logger.warning(f"No se pudo verificar disponibilidad de cupos: {e}")

            # retries = 0
            # max_retries = 3
            # success = False

            # while retries < max_retries and not success:
            #     try:
            #         book_button = await page.querySelector('button.ant-btn-primary')
            #         if book_button:
            #             await book_button.click()
            #         else:
            #             raise Exception("No se encontró el botón de reserva.")
            #     except Exception as e:
            #         logger.error(f"Error al hacer clic en el botón de reserva: {e}")
            #         break

            #     logger.info(f"Intento de reserva #{retries + 1}")
            #     await asyncio.sleep(5)

            #     # Esperar mensaje de confirmación o error
            #     try:
            #         await page.waitForSelector('.ant-message-notice', {'timeout': 5000})
            #         messages = await page.querySelectorAll('.ant-message-notice')
            #         for msg in messages:
            #             msg_text = await page.evaluate('(el) => el.innerText', msg)
            #             logger.info(f"Mensaje recibido: {msg_text}")
            #             if "confirmado" in msg_text.lower() or "reservado" in msg_text.lower():
            #                 logger.info("Reserva confirmada.")
            #                 success = True
            #                 break
            #     except Exception:
            #         logger.warning("No se detectó mensaje de confirmación en este intento.")

            #     retries += 1

            # if not success:
            #     logger.error("No se pudo confirmar la reserva después de varios intentos.")
        # Intentar reservar si no está lleno ni reservado
        retries = 0
        max_retries = 3
        success = False

        while retries < max_retries and not success:
            try:
                book_button = await page.querySelector('button.ant-btn-primary')
                if book_button:
                    await book_button.click()
                else:
                    raise Exception("No se encontró el botón de reserva.")
            except Exception as e:
                logger.error(f"Error al hacer clic en el botón de reserva: {e}")
                break

            logger.info(f"Intento de reserva #{retries + 1}")
            await asyncio.sleep(5)

            # Verificar si ya se confirmó la reserva
            try:
                modal_text = await page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('.ant-modal-content span')).map(el => el.innerText.toLowerCase());
                }''')
                if any("reserved" in t for t in modal_text) or any("cancel booking" in t for t in modal_text):
                    logger.info(f"Reserva confirmada para '{class_name}'. Cerrando modal.")
                    success = True
                    break
            except Exception:
                logger.warning("No se detectó estado confirmado tras intentar reservar.")

            retries += 1

        if not success:
            logger.error(f"No se pudo confirmar la reserva para '{class_name}' después de varios intentos.")
        await page.click('.ant-modal-close')
    except Exception as e:
        logger.error(f"Error al verificar modal o hacer clic en Book: {e}")



async def login_boxmagic(page, email, password):
    logger.info("Esperando campos de login...")
    # Espera que los inputs estén disponibles
    await page.waitForSelector('input[placeholder="Correo"]', {'timeout': 15000})
    await page.waitForSelector('input[placeholder="Contraseña"]', {'timeout': 15000})

    logger.info("Rellenando email y contraseña...")

    # Escribir correo
    await page.type('input[placeholder="Correo"]', email, {'delay': 50})
    
    # Escribir contraseña
    await page.type('input[placeholder="Contraseña"]', password, {'delay': 50})

    # Hacer clic en el botón "Ingresar"
    await page.click('button.ant-btn-primary')

    logger.info("Formulario de login enviado")
    await asyncio.sleep(10)  # Esperar navegación/redirección

    logger.info("Haciendo clic en el botón de Clases...")

    try:
        await page.waitForSelector('#tabClases', {'timeout': 10000})
        await page.click('#tabClases')
        logger.info("Navegación a Clases completada.")
        await asyncio.sleep(3)  # Esperar a que cargue la página de clases
    except Exception as e:
        logger.error(f"No se pudo hacer clic en Clases: {e}")
     
    logger.info("Desplazándose hasta la última fecha visible...")
    try:
        seen_days = set()
        attempts = 0
        max_attempts = 20  # prevent infinite loop in case of UI issues

        while attempts < max_attempts:
            elements = await page.querySelectorAll('.fecha .diaNumero')
            if not elements:
                logger.warning("No se encontraron elementos de fecha.")
                break

            last_element = elements[-1]
            last_day_text = await page.evaluate('(el) => el.innerText', last_element)

            if last_day_text in seen_days:
                logger.info(f"No se encontraron nuevas fechas después de hacer clic en: {last_day_text}")
                break

            seen_days.add(last_day_text)

            try:
                await last_element.click()
                logger.info(f"Clic en la fecha: {last_day_text}")
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"No se pudo hacer clic en la fecha: {e}")
                break

            attempts += 1
        
        # Detectar el día seleccionado desde la interfaz
        try:
            selected_day_element = await page.querySelector('.fecha.activa .diaSemana')
            selected_day_text = await page.evaluate('(el) => el.innerText.trim().toLowerCase()', selected_day_element)
        except Exception as e:
            logger.error(f"No se pudo determinar el día de la semana desde el encabezado: {e}")
            return

        logger.info(f"Día seleccionado: {selected_day_text}")

        if selected_day_text == "ma":  # martes
            clases_a_reservar = [
                "CrossFit Park 87 07:00-08:00",
                "Weightlifting (Park 87) 17:30-18:30",
                "Gymnastics (Park 87) 18:30-19:30"
            ]
        else:
            clases_a_reservar = ["CrossFit Park 87 07:00-08:00"]

        for clase in clases_a_reservar:
            await reserve_clase(clase, page)

        # Logout after reservation loop
        await logout_boxmagic(page)


    except Exception as e:
        logger.error(f"No se pudo navegar hasta la última fecha: {e}")

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
        
        page_ready_event = asyncio.Event()
        stop_script_flag = {'stop': False}

        # Go to the initial page
        await page.goto('https://go.boxmagic.app/bienvenida/entrada/?modo=ingreso', waitUntil='networkidle2')

        await login_boxmagic(page, "mariazelhdezn@gmail.com", "MHdezN0409")
    
    finally:
        await browser.close()

# --- Logout function for Boxmagic ---
async def logout_boxmagic(page):
    logger.info("Iniciando proceso de cierre de sesión...")
    try:
        await page.waitForSelector('#tabUsuarioCompu', {'timeout': 10000})
        await page.click('#tabUsuarioCompu')
        logger.info("Perfil de usuario abierto.")
        await asyncio.sleep(2)
        await page.waitForSelector('a.btn-air-danger', {'timeout': 10000})
        await page.click('a.btn-air-danger')
        logger.info("Cierre de sesión exitoso.")
    except Exception as e:
        logger.error(f"Error al cerrar sesión: {e}")

if __name__ == '__main__':
    asyncio.run(main())
