import asyncio
import os
import json
PYPPETEER_CHROMIUM_REVISION = '1263111'
os.environ['PYPPETEER_CHROMIUM_REVISION'] = PYPPETEER_CHROMIUM_REVISION
from pyppeteer import launch
import time
import requests
from pyppeteer_stealth import stealth

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
                        return select ? select.options[0].value : null;
                    }''')
                    if from_value:
                        await page.select('#P500_FISCAL_YEAR_FROM', from_value)
                        print(f"Selected first option in #P500_FISCAL_YEAR_FROM: {from_value}")
                        break
                except Exception as e:
                    print(f"Attempt {attempt + 1} failed for #P500_FISCAL_YEAR_FROM: {e}")
                    await asyncio.sleep(5)

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
                    await asyncio.sleep(5)

            if not from_value or not to_value:
                raise Exception("Unable to select fiscal year values after multiple attempts.")

            await page.waitForSelector('#B106150366531214971', timeout=60000)
            await page.click('#B106150366531214971')
            print("Submit button.")

            await asyncio.sleep(10)

            await page.waitForSelector('#B47528447156014705', timeout=60000)
            await page.click('#B47528447156014705')
            print("Download button.")

            await page.waitForNavigation(waitUntil='networkidle0', timeout=60000)
            print("Navigation after button click is complete.")

            await page.screenshot({'path': 'after_interaction.png'})

        except Exception as e:
            print("An error occurred during interactions:", e)
            import traceback
            traceback.print_exc()
            # await browser.close()

    page.on('console', lambda msg: asyncio.ensure_future(console_message_handler(msg)))

    await page.goto('https://service.yukon.ca/apps/contract-registry', waitUntil='networkidle2')

    await asyncio.sleep(45)

asyncio.get_event_loop().run_until_complete(main())