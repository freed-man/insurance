
import importlib.metadata

from playwright.sync_api import sync_playwright

MIB_URL = "https://enquiry.navigate.mib.org.uk/checkyourvehicle"


def main():
    try:
        print("Playwright package version:",
              importlib.metadata.version("playwright"))

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)

            try:
                print("Chromium version:", browser.version)

                page = browser.new_page()

                print("Browser user-agent:",
                      page.evaluate("navigator.userAgent"))

                response = page.goto(
                    MIB_URL,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )

                if response is None:
                    print("No main-document response received.")
                else:
                    print("HTTP status:", response.status)
                    print("Final URL:", page.url)
                    print("Page title:", page.title())

                    headers = response.headers

                    for name in (
                        "server",
                        "content-type",
                        "date",
                        "via",
                        "x-azure-ref",
                        "x-cache",
                    ):
                        if name in headers:
                            print(f"{name}: {headers[name]}")

                    print("\nFirst 500 characters of page text:")
                    print(page.locator("body").inner_text()[:500])

            finally:
                browser.close()

    except Exception as exc:
        print("Diagnostic failed:", type(exc).__name__, str(exc))


if __name__ == "__main__":
    main()
