
from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        page = browser.new_page()
        page.goto("http://127.0.0.1:8000/", wait_until="domcontentloaded")

        print("Page title:", page.title())
        print("Page URL:", page.url)
        print("Registration field found:", page.locator(
            "#registration"
        ).count() == 1)

        browser.close()


if __name__ == "__main__":
    main()