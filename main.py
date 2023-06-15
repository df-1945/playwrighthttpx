from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from bs4 import BeautifulSoup
from pydantic import BaseModel
import asyncio
import httpx
import time
from playwright.async_api import async_playwright
from pathlib import Path
from enum import Enum
import psutil
from typing import List, Dict, Optional

app = FastAPI()

origins = [
    "https://kikisan.pages.dev",
    "https://kikisan.site",
    "https://www.kikisan.site",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Status(str, Enum):
    def __str__(self):
        return str(self.value)

    BARU = "baru"
    LAMA = "lama"


class Hasil(BaseModel):
    keyword: str
    pages: int
    status: Status
    upload: str
    download: str
    time: float
    cpu_max_percent: Optional[float] = None
    ram_max_percent: Optional[float] = None
    jumlah: int
    hasil: Optional[List[Dict]] = None


class DataRequest(BaseModel):
    keyword: str
    pages: int


data_playwrighthttpx = []


@app.post("/playwrighthttpx")
def input_playwrighthttpx(request: Request, input: DataRequest):
    try:
        sent_bytes_start, received_bytes_start = get_network_usage()

        headers = {"User-Agent": request.headers.get("User-Agent")}

        start_time = time.time()
        hasil = asyncio.run(main(headers, input.keyword, input.pages))
        end_time = time.time()

        sent_bytes_end, received_bytes_end = get_network_usage()

        sent_bytes_total = sent_bytes_end - sent_bytes_start
        received_bytes_total = received_bytes_end - received_bytes_start

        print("Total Penggunaan Internet:")
        print("Upload:", format_bytes(sent_bytes_total))
        print("Download:", format_bytes(received_bytes_total))

        print(
            f"Berhasil mengambil {len(hasil)} produk dalam {end_time - start_time} detik."
        )
        data = {
            "keyword": input.keyword,
            "pages": input.pages,
            "status": "baru",
            "upload": format_bytes(sent_bytes_total),
            "download": format_bytes(received_bytes_total),
            "time": end_time - start_time,
            "jumlah": len(hasil),
            "hasil": hasil,
        }
        data_playwrighthttpx.append(data)
        return data_playwrighthttpx
    except Exception as e:
        return e


async def main(headers, keyword, pages):
    product_soup = []
    async with async_playwright() as playwright:
        # browser = await playwright.chromium.launch(headless=False)
        # context = await browser.new_context()
        path_to_extension = Path(__file__).parent.joinpath("my-extension")
        context = await playwright.chromium.launch_persistent_context(
            "",
            headless=False,
            args=[
                "--headless=new",  # the new headless arg for chrome v109+. Use '--headless=chrome' as arg for browsers v94-108.
                f"--disable-extensions-except={path_to_extension}",
                f"--load-extension={path_to_extension}",
            ],
        )
        loop = asyncio.get_event_loop()
        tasks = [
            loop.create_task(
                scrape(
                    f"https://www.tokopedia.com/search?q={keyword}&page={page}", context
                )
            )
            for page in range(1, pages + 1)
        ]
        for task in asyncio.as_completed(tasks):
            page_product_soup = await task
            product_soup.extend(page_product_soup)
        # await browser.close()
    async with httpx.AsyncClient() as session:
        chunk_size = int(len(product_soup) / 5)
        product_soup_chunks = [[] for _ in range(chunk_size)]

        # membagi link secara round-robin
        for i, soup in enumerate(product_soup):
            product_soup_chunks[i % chunk_size].append(soup)

        tasks = []
        for i in range(chunk_size):
            tasks.append(
                asyncio.create_task(
                    scrape_produk(product_soup_chunks[i], headers, session)
                )
            )

        tasks = await asyncio.gather(*tasks)

    combined_data = []
    for array in tasks:
        if array:
            combined_data.extend(array)
    return combined_data


async def scrape(url, context):
    soup_produk = []
    try:
        page = await context.new_page()
        print(f"Membuka halaman {url}...")
        await page.goto(url, timeout=1800000)
        print(f"Menunggu reload {url}...")
        await page.wait_for_load_state("networkidle", timeout=1800000)
        # await page.wait_for_selector(".css-jza1fo", timeout=1800000)
        await scroll(page, 1000)
        content = await page.content()
        soup = BeautifulSoup(content, "html.parser")
        product_selectors = [
            ("div", {"class": "css-kkkpmy"}),
            ("div", {"class": "css-llwpbs"}),
        ]
        for selector in product_selectors:
            tag, attrs = selector
            products = soup.find_all(tag, attrs)
            for product in products:
                link = product.find("a", {"class": "pcv3__info-content css-gwkf0u"})
                if link:
                    soup_produk.append(product)
        print(f"Berhasil scrape data dari halaman {page}.")
        await page.close()
        return soup_produk
    except Exception as e:
        print(f"Terjadi kesalahan saat mengakses halaman {url}: {str(e)}")


async def scroll(page, scroll_amount):
    try:
        prev_height = await page.evaluate("document.documentElement.scrollTop")
        while True:
            await page.wait_for_selector(".css-974ipl", timeout=1800000)
            await page.evaluate(f"window.scrollBy(0, {scroll_amount});")
            curr_height = await page.evaluate("document.documentElement.scrollTop")
            if prev_height == curr_height:
                break
            prev_height = curr_height
            print("Scrolling...")
    except Exception as e:
        print(f"Terjadi kesalahan saat Scrolling: {str(e)}")


async def scrape_produk(product_soup, headers, session):
    tasks = [scrape_page(soup, headers, session) for soup in product_soup]
    tasks = await asyncio.gather(*tasks)
    return tasks


async def scrape_page(soup, headers, session):
    href = soup.find("a")["href"]
    link_parts = href.split("r=")
    r_part = link_parts[-1]
    link_part = r_part.split("&")
    r_part = link_part[0]
    new_link = f"{r_part.replace('%3A', ':').replace('%2F', '/')}"
    new_link = new_link.split("%3FextParam")[0]
    new_link = new_link.split("%3Fsrc")[0]
    new_link = new_link.split("?extParam")[0]
    results = []
    results.append(asyncio.create_task(data_product(soup, new_link, headers, session)))
    results.append(
        asyncio.create_task(
            data_shop("/".join(new_link.split("/")[:-1]), headers, session)
        )
    )
    results = await asyncio.gather(*results)
    combined_data = {}
    for data in results:
        if data:
            combined_data.update(data)
    return combined_data


async def data_product(soup_produk, product_link, headers, session):
    try_count = 0
    while try_count < 5:
        try:
            response = await session.get(product_link, headers=headers, timeout=30.0)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")

            data_to_scrape = {
                "link_product": "",
                "product_name": ("h1", {"class": "css-1os9jjn"}),
                "product_price": ("div", {"class": "price"}),
                "product_terjual": (
                    "span",
                    {"class": "prd_label-integrity css-1duhs3e"},
                ),
                "product_rating": (
                    "span",
                    {"class": "prd_rating-average-text css-t70v7i"},
                ),
                "product_diskon": (
                    "div",
                    {"class": "prd_badge-product-discount css-1qtulwh"},
                ),
                "price_ori": (
                    "div",
                    {"class": "prd_label-product-slash-price css-1u1z2kp"},
                ),
                "product_items": ("div", {"class": "css-1b2d3hk"}),
                "product_detail": ("li", {"class": "css-bwcbiv"}),
                "product_keterangan": ("span", {"class": "css-168ydy0 eytdjj01"}),
            }

            results = {}

            for key, value in data_to_scrape.items():
                if key == "product_detail":
                    tag, attrs = value
                    elements = soup.find_all(tag, attrs)
                    results[key] = [element.text.strip() for element in elements]
                elif key == "product_items":
                    tag, attrs = value
                    elements = soup.find_all(tag, attrs)
                    if elements:
                        for key_element in elements:
                            items = key_element.find_all(
                                "div", {"class": "css-1y1bj62"}
                            )
                            kunci = (
                                key_element.find(
                                    "p", {"class": "css-x7tz35-unf-heading e1qvo2ff8"}
                                )
                                .text.strip()
                                .split(":")[0]
                            )
                            results[kunci] = [
                                item.text.strip()
                                for item in items
                                if item.text.strip()
                                != ".css-1y1bj62{padding:4px 2px;display:-webkit-inline-box;display:-webkit-inline-flex;display:-ms-inline-flexbox;display:inline-flex;}"
                            ]
                    else:
                        results[key] = None
                elif key == "link_product":
                    results[key] = product_link
                elif (
                    key == "product_terjual"
                    or key == "product_rating"
                    or key == "product_diskon"
                    or key == "price_ori"
                ):
                    tag, attrs = value
                    element = soup_produk.find(tag, attrs)
                    if element:
                        results[key] = element.text.strip()
                    else:
                        results[key] = None
                else:
                    tag, attrs = value
                    element = soup.find(tag, attrs)
                    if element:
                        text = element.get_text(
                            separator="<br>"
                        )  # Gunakan separator '\n' untuk menambahkan baris baru
                        results[key] = text.strip()
                    else:
                        results[key] = None
            print(f"Berhasil scrape data produk dari halaman {product_link}")
            return results

        except (httpx.ConnectTimeout, httpx.TimeoutException, httpx.HTTPError):
            try_count += 1
            print(f"Koneksi ke halaman {product_link} timeout. Mencoba lagi...")
    else:
        print(
            f"Gagal melakukan koneksi ke halaman {product_link} setelah mencoba beberapa kali."
        )
        return None


async def data_shop(shop_link, headers, session):
    try_count = 0
    while try_count < 5:
        try:
            response = await session.get(shop_link, headers=headers, timeout=30.0)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")

            data_to_scrape = {
                "link_shop": "",
                "shop_name": ("h1", {"class": "css-1g675hl"}),
                "shop_status": ("span", {"data-testid": "shopSellerStatusHeader"}),
                "shop_location": ("span", {"data-testid": "shopLocationHeader"}),
                "shop_info": ("div", {"class": "css-6x4cyu e1wfhb0y1"}),
            }

            results = {}

            for key, value in data_to_scrape.items():
                if key == "link_shop":
                    results[key] = shop_link
                elif key == "shop_status":
                    tag, attrs = value
                    element = soup.find(tag, attrs)
                    time = soup.find("strong", {"class": "time"})
                    if time:
                        waktu = time.text.strip()
                        status = element.text.strip()
                        results[key] = status + " " + waktu
                    elif element:
                        results[key] = element.text.strip()
                    else:
                        results[key] = None
                elif key == "shop_info":
                    tag, attrs = value
                    elements = soup.find_all(tag, attrs)
                    key = ["rating_toko", "respon_toko", "open_toko"]
                    ket = soup.find_all(
                        "p", {"class": "css-1dzsr7-unf-heading e1qvo2ff8"}
                    )
                    i = 0
                    for item, keterangan in zip(elements, ket):
                        if item and keterangan:
                            results[key[i]] = (
                                item.text.replace("\u00b1", "").strip()
                                + " "
                                + keterangan.text.strip()
                            )
                        else:
                            results[key[i]] = None
                        i += 1
                else:
                    tag, attrs = value
                    element = soup.find(tag, attrs)
                    if element:
                        results[key] = element.text.strip()
                    else:
                        results[key] = None
            print(f"Berhasil scrape data toko dari halaman {shop_link}")
            return results

        except (httpx.ConnectTimeout, httpx.TimeoutException, httpx.HTTPError):
            try_count += 1
            print(f"Koneksi ke halaman {shop_link} timeout. Mencoba lagi...")
    else:
        print(
            f"Gagal melakukan koneksi ke halaman {shop_link} setelah mencoba beberapa kali."
        )
        return None


def get_network_usage():
    network_stats = psutil.net_io_counters()
    sent_bytes = network_stats.bytes_sent
    received_bytes = network_stats.bytes_recv

    return sent_bytes, received_bytes


def format_bytes(bytes):
    # Fungsi ini mengubah ukuran byte menjadi format yang lebih mudah dibaca
    sizes = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while bytes >= 1024 and i < len(sizes) - 1:
        bytes /= 1024
        i += 1
    return "{:.2f} {}".format(bytes, sizes[i])


@app.get("/data")
def ambil_data(
    keyword: Optional[str] = None,
    pages: Optional[int] = None,
    status: Optional[Status] = None,
):
    if status is not None or keyword is not None or pages is not None:
        result_filter = []
        for data in data_playwrighthttpx:
            data = Hasil.parse_obj(data)
            if (
                status == data.status
                and data.keyword == keyword
                and data.pages == pages
            ):
                result_filter.append(data)
    else:
        result_filter = data_playwrighthttpx
    return result_filter


@app.put("/monitoring")
def ambil_data(input: DataRequest):
    for data in data_playwrighthttpx:
        if (
            data["status"] == "baru"
            and data["keyword"] == input.keyword
            and data["pages"] == input.pages
        ):
            cpu_percent_max = 0  # Highest CPU usage during execution
            ram_percent_max = 0  # Highest RAM usage during execution

            interval = 0.1  # Interval for monitoring (seconds)
            duration = data["time"]
            num_intervals = int(duration / interval) + 1

            for _ in range(num_intervals):
                cpu_percent = psutil.cpu_percent(interval=interval)
                print("cpu", cpu_percent)
                ram_percent = psutil.virtual_memory().percent
                print("ram", ram_percent)

                if cpu_percent > cpu_percent_max:
                    cpu_percent_max = cpu_percent

                if ram_percent > ram_percent_max:
                    ram_percent_max = ram_percent

            data["cpu_max_percent"] = cpu_percent_max
            data["ram_max_percent"] = ram_percent_max
            data["status"] = "lama"

        else:
            data = None

    return data
