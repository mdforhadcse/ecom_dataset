import csv
import re
import time
import random
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import urljoin
from contextlib import suppress

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException, ElementClickInterceptedException

# NOTE: use the provided Firefox driver factory
from firefox_driver_logged_in import create_logged_in_firefox


LISTING_CONTAINER_CSS = 'div[data-qa-locator="general-products"]'
LISTING_CARD_CSS = 'div.Bm3ON[data-qa-locator="product-item"][data-tracking="product-card"]'

STAR_FILLED_TOKEN = "TB19ZvE"  # token inside filled-star image URL per Notion example


def _rand_sleep(a=0.7, b=1.8):
    time.sleep(random.uniform(a, b))


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text_or_empty(el) -> str:
    try:
        return el.text.strip()
    except Exception:
        return ""


def _get_attr(el, name: str) -> str:
    try:
        v = el.get_attribute(name)
        return v.strip() if v else ""
    except Exception:
        return ""


def _int_from_text(text: str) -> Optional[int]:
    m = re.search(r"(\d[\d,]*)", text or "")
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def _price_to_number(text: str) -> Optional[float]:
    # Keep digits and dot
    cleaned = re.sub(r"[^0-9.]", "", text or "")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def wait_css(driver, css: str, timeout: int = 12):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, css))
    )


def wait_all_css(driver, css: str, timeout: int = 12):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_all_elements_located((By.CSS_SELECTOR, css))
    )


# --- Helper: Safe get and popup close ---
def safe_get(driver, url: str, ready_css: Optional[str] = None, tries: int = 3, wait_timeout: int = 12) -> bool:
    """
    Navigate to a URL with retries. If page load times out, we call window.stop()
    and still attempt to locate a readiness CSS. Returns True if navigation appears
    successful (ready_css present or no ready_css specified), else False.
    """
    for attempt in range(1, tries + 1):
        try:
            driver.get(url)
        except TimeoutException:
            with suppress(Exception):
                driver.execute_script("window.stop();")
        # If no specific readiness check, consider this a success.
        if not ready_css:
            return True
        # Try to confirm readiness
        try:
            wait_css(driver, ready_css, timeout=wait_timeout)
            return True
        except TimeoutException:
            # Try to stop loading and retry once more
            with suppress(Exception):
                driver.execute_script("window.stop();")
            if attempt == tries:
                return False
            time.sleep(min(2 * attempt, 4))
    return False


def maybe_close_popups(driver):
    """
    Best-effort close of common consent / notification popups that might block clicks.
    Non-fatal if nothing is found.
    """
    try:
        # Try common cookie/consent accept buttons
        candidates = driver.find_elements(By.CSS_SELECTOR, "button, a")
        for el in candidates[:50]:
            label = _text_or_empty(el).lower()
            if label in {"accept all", "accept cookies", "allow all", "i agree", "got it", "ok"}:
                with suppress(Exception):
                    driver.execute_script("arguments[0].click();", el)
                    _rand_sleep(0.2, 0.5)
                    break
    except Exception:
        pass


def normalize_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    return href


def scroll_to_bottom(driver, max_steps: int = 6):
    last_height = driver.execute_script("return document.body.scrollHeight")
    for _ in range(max_steps):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        _rand_sleep(0.5, 1.0)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height


def extract_listing_cards(driver) -> List[Dict]:
    """
    On a listing page, extract minimal info from each product card.
    """
    try:
        wait_css(driver, LISTING_CONTAINER_CSS, timeout=15)
    except TimeoutException:
        return []

    scroll_to_bottom(driver, max_steps=3)  # help lazy load
    cards = driver.find_elements(By.CSS_SELECTOR, LISTING_CARD_CSS)
    results = []

    for card in cards:
        try:
            # Product detail link + main image
            anchor = card.find_element(By.CSS_SELECTOR, 'div._95X4G a[href]')
            product_url = normalize_url(_get_attr(anchor, "href"))
            # Sometimes URLs are protocol-relative; normalize
            product_url = urljoin("https://www.daraz.com.bd/", product_url)

            img = card.find_element(By.CSS_SELECTOR, 'div.picture-wrapper img[type="product"]')
            product_img = _get_attr(img, "src")

            # Title (optional but helpful as fallback)
            title_el = card.find_element(By.CSS_SELECTOR, 'div.RfADt a')
            title_text = _text_or_empty(title_el)

            # Price
            price_el = card.find_element(By.CSS_SELECTOR, 'div.aBrP0 span.ooOxS')
            price_text = _text_or_empty(price_el)
            product_price = _price_to_number(price_text)

            # Total sold (e.g., "100 sold")
            total_sold = None
            try:
                sold_el = card.find_element(By.CSS_SELECTOR, 'span._1cEkb span')
                total_sold = _int_from_text(_text_or_empty(sold_el))
            except NoSuchElementException:
                pass

            # Seller location
            seller_location = ""
            try:
                loc_el = card.find_element(By.CSS_SELECTOR, 'span.oa6ri')
                seller_location = _text_or_empty(loc_el)
            except NoSuchElementException:
                pass

            results.append(
                {
                    "product_url": product_url,
                    "product_img": product_img,
                    "product_price": product_price,
                    "total_sold": total_sold,
                    "seller_location": seller_location,
                    "listing_title": title_text,
                }
            )
        except NoSuchElementException:
            continue
        except StaleElementReferenceException:
            continue

    return results


def product_has_reviews(driver) -> (bool, Optional[int]):
    """
    Check the pdp review summary link text:
    - "No Ratings" => False
    - "Ratings 8"  => True, return 8
    """
    try:
        link = wait_css(driver, 'a.pdp-review-summary__link', timeout=10)
        txt = _text_or_empty(link)
        if "No Ratings" in txt:
            return False, None
        # Extract number after "Ratings"
        total = _int_from_text(txt)
        return True, total
    except TimeoutException:
        # Fallback to small badge near stars (e.g., "(8)")
        try:
            small = driver.find_element(By.CSS_SELECTOR, "span.qzqFw")
            total = _int_from_text(_text_or_empty(small))
            return (total is not None and total > 0), total
        except NoSuchElementException:
            return False, None


def extract_product_level_details(driver) -> Dict:
    """
    Extract product-level details on PDP.
    """
    # Product name
    try:
        name_el = wait_css(driver, "h1.pdp-mod-product-badge-title", timeout=12)
        product_name = _text_or_empty(name_el)
    except TimeoutException:
        product_name = ""

    # Seller name
    seller_name = ""
    try:
        seller_el = driver.find_element(By.CSS_SELECTOR, "a.seller-name__detail-name")
        seller_name = _text_or_empty(seller_el)
    except NoSuchElementException:
        pass

    # Positive Seller Ratings (e.g., "83%")
    positive_seller_ratings = ""
    try:
        psr_el = driver.find_element(By.CSS_SELECTOR, "div.seller-info-value.rating-positive")
        positive_seller_ratings = _text_or_empty(psr_el)
    except NoSuchElementException:
        pass

    # Breadcrumb categories (skip last = product name)
    product_categories = []
    try:
        crumbs = driver.find_elements(By.CSS_SELECTOR, "ul#J_breadcrumb li .breadcrumb_item_anchor span")
        product_categories = [c.text.strip() for c in crumbs if _text_or_empty(c)]
    except Exception:
        pass

    # Gallery thumbnails: collect all src
    product_image_urls: List[str] = []
    try:
        thumbs = driver.find_elements(By.CSS_SELECTOR, "img.pdp-mod-common-image.item-gallery__thumbnail-image")
        for t in thumbs:
            src = _get_attr(t, "src")
            if src:
                product_image_urls.append(src)
    except Exception:
        pass

    # Overall rating like "5.0/5"
    overall_rating = ""
    try:
        avg = driver.find_element(By.CSS_SELECTOR, "div.score span.score-average")
        mx = driver.find_element(By.CSS_SELECTOR, "div.score span.score-max")
        overall_rating = f"{_text_or_empty(avg)}{_text_or_empty(mx)}"
    except NoSuchElementException:
        pass

    # Rating summary (5->1)
    rating_summary_map = {}
    try:
        rows = driver.find_elements(By.CSS_SELECTOR, "div.detail ul li")
        # order in UI: 5*, 4*, 3*, 2*, 1*
        stars = [5, 4, 3, 2, 1]
        for idx, li in enumerate(rows[:5]):
            try:
                cnt_el = li.find_element(By.CSS_SELECTOR, "span.percent")
                cnt = _int_from_text(_text_or_empty(cnt_el)) or 0
                rating_summary_map[f"{stars[idx]}_star"] = cnt
            except NoSuchElementException:
                rating_summary_map[f"{stars[idx]}_star"] = 0
    except Exception:
        pass

    return {
        "product_name": product_name,
        "seller_name": seller_name,
        "positive_seller_ratings": positive_seller_ratings,
        "product_categories": product_categories,
        "product_image_urls": product_image_urls,
        "overall_rating": overall_rating,
        "rating_summary_map": rating_summary_map,
    }


def _extract_background_image_url(style_value: str) -> Optional[str]:
    if not style_value:
        return None
    # style='background-image: url("https://...jpg");'
    m = re.search(r'url\((["\']?)(.+?)\1\)', style_value)
    return m.group(2) if m else None


def iterate_all_reviews(driver) -> List[Dict]:
    """
    Iterate reviews under <div class="mod-reviews"> across pagination.
    Returns list of dicts per review.
    """
    reviews: List[Dict] = []
    try:
        reviews_root = wait_css(driver, "div.mod-reviews", timeout=10)
    except TimeoutException:
        return reviews  # no visible reviews section

    def extract_page_items():
        items = reviews_root.find_elements(By.CSS_SELECTOR, "div.item")
        page_results = []
        for it in items:
            try:
                # rating by counting filled star images in this review item
                star_imgs = it.find_elements(By.CSS_SELECTOR, "div.container-star.starCtn.left img.star")
                filled = 0
                for s in star_imgs:
                    src = _get_attr(s, "src")
                    if STAR_FILLED_TOKEN in (src or ""):
                        filled += 1

                # date
                date_text = ""
                try:
                    date_el = it.find_element(By.CSS_SELECTOR, "div.top span.title.right")
                    date_text = _text_or_empty(date_el)
                except NoSuchElementException:
                    pass

                # username
                username = ""
                try:
                    mid_spans = it.find_elements(By.CSS_SELECTOR, "div.middle > span")
                    if mid_spans:
                        username = _text_or_empty(mid_spans[0])
                except NoSuchElementException:
                    pass

                # review text
                review_text = ""
                try:
                    content_el = it.find_element(By.CSS_SELECTOR, "div.item-content div.content")
                    review_text = _text_or_empty(content_el)
                except NoSuchElementException:
                    pass

                # review images (comma-separated)
                review_imgs: List[str] = []
                try:
                    imgs = it.find_elements(By.CSS_SELECTOR, "div.review-image__list div.image")
                    for img_div in imgs:
                        style_val = _get_attr(img_div, "style")
                        url = _extract_background_image_url(style_val)
                        if url:
                            review_imgs.append(url)
                except NoSuchElementException:
                    pass

                # likes
                likes = 0
                try:
                    left_content = it.find_element(By.CSS_SELECTOR, "div.bottom span.left-content")
                    # the last span inside left-content holds the number
                    spans = left_content.find_elements(By.CSS_SELECTOR, "span")
                    if spans:
                        likes_txt = _text_or_empty(spans[-1])
                        likes = _int_from_text(likes_txt) or 0
                except NoSuchElementException:
                    pass

                page_results.append(
                    {
                        "reviewer": username,
                        "rating_number": filled,
                        "review_text": review_text,
                        "review_images": review_imgs,
                        "review_date": date_text,
                        "review_like": likes,
                    }
                )
            except StaleElementReferenceException:
                continue
        return page_results

    # First page
    reviews.extend(extract_page_items())

    # Next page button
    while True:
        try:
            next_btn = driver.find_element(
                By.CSS_SELECTOR,
                "button.next-btn.next-btn-normal.next-btn-medium.next-pagination-item.next",
            )
        except NoSuchElementException:
            break  # no more pages

        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", next_btn)
            _rand_sleep(0.2, 0.5)
            next_btn.click()
            _rand_sleep(1.2, 2.0)
            # refresh root (content may re-render)
            try:
                reviews_root = wait_css(driver, "div.mod-reviews", timeout=8)
            except TimeoutException:
                break
            reviews.extend(extract_page_items())
        except (ElementClickInterceptedException, StaleElementReferenceException):
            break

    return reviews


def write_reviews_to_csv(csv_path: str, rows: List[Dict], header: List[str], append: bool = True):
    mode = "a" if append else "w"
    with open(csv_path, mode, newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        if not append:
            writer.writeheader()
        for r in rows:
            writer.writerow(r)


def scrape_range(start_page: int = 1, end_page: int = 102, out_csv: str = "daraz_reviews.csv"):
    driver = create_logged_in_firefox()
    driver.set_page_load_timeout(90)

    headers = [
        "Reviewer username or name",
        "Rating (number)",
        "Review (text)",
        "Review image",
        "Review date (date time)",
        "Review like (number like on that review)",
        "Product name",
        "Product category",
        "Product price",
        "Total sold",
        "Total rating",
        "Overall rating",
        "Rating summery",
        "product image url",
        "seller name",
        "Positive Seller Ratings",
        "Seller location",
        "Data source (link of the page)",
        "Processing time (Current timestamp)",
    ]

    # initialize CSV with header
    write_reviews_to_csv(out_csv, [], headers, append=False)

    try:
        for page in range(start_page, end_page + 1):
            listing_url = f"https://www.daraz.com.bd/all-products/?page={page}"
            print(f"[Listing] {listing_url}")
            if not safe_get(driver, listing_url, LISTING_CONTAINER_CSS, tries=3, wait_timeout=15):
                print("  Failed to load listing (timeout). Skipping this page.")
                continue
            maybe_close_popups(driver)
            _rand_sleep()

            cards = extract_listing_cards(driver)
            print(f"  Found {len(cards)} product cards on page {page}")

            for idx, card in enumerate(cards, start=1):
                product_url = card["product_url"]
                print(f"    [{idx}/{len(cards)}] {product_url}")
                if not product_url:
                    continue

                # Navigate to PDP with retries and graceful timeout handling
                if not safe_get(driver, product_url, "h1.pdp-mod-product-badge-title", tries=3, wait_timeout=15):
                    print("      Product page load timed out — skipping.")
                    continue
                maybe_close_popups(driver)
                _rand_sleep()

                has_reviews, total_rating = product_has_reviews(driver)
                if not has_reviews:
                    print("      No ratings — skipping product.")
                    continue

                pd = extract_product_level_details(driver)

                # Gather all reviews
                reviews = iterate_all_reviews(driver)
                if not reviews:
                    print("      No review items extracted despite ratings.")
                    continue

                # Assemble rows for CSV
                rows_to_write = []
                for rv in reviews:
                    rows_to_write.append(
                        {
                            "Reviewer username or name": rv["reviewer"],
                            "Rating (number)": rv["rating_number"],
                            "Review (text)": rv["review_text"],
                            "Review image": ", ".join(rv["review_images"]) if rv["review_images"] else "",
                            "Review date (date time)": rv["review_date"],
                            "Review like (number like on that review)": rv["review_like"],
                            "Product name": pd["product_name"] or card["listing_title"],
                            "Product category": ", ".join(pd["product_categories"]),
                            "Product price": card["product_price"] if card["product_price"] is not None else "",
                            "Total sold": card["total_sold"] if card["total_sold"] is not None else "",
                            "Total rating": total_rating if total_rating is not None else "",
                            "Overall rating": pd["overall_rating"],
                            "Rating summery": ", ".join(
                                f"{k.replace('_star',' star')}: {v}"
                                for k, v in pd["rating_summary_map"].items()
                            ) if pd["rating_summary_map"] else "",
                            "product image url": ", ".join(pd["product_image_urls"]) if pd["product_image_urls"] else card["product_img"],
                            "seller name": pd["seller_name"],
                            "Positive Seller Ratings": pd["positive_seller_ratings"],
                            "Seller location": card["seller_location"],
                            "Data source (link of the page)": product_url,
                            "Processing time (Current timestamp)": _now_iso(),
                        }
                    )

                write_reviews_to_csv(out_csv, rows_to_write, headers, append=True)
                print(f"      Wrote {len(rows_to_write)} review rows.")

                # polite delay to reduce load / detection
                _rand_sleep(1.2, 2.4)

            # small delay between listing pages
            _rand_sleep(1.5, 3.0)

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    # Defaults: 1..102 per your instruction
    scrape_range(1, 1, out_csv="daraz_reviews.csv")
