from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, validator
import asyncio
import httpx
import time
from playwright.async_api import async_playwright
from pathlib import Path
from enum import Enum
import psutil

# from typing import List, Dict, Optional
import cpuinfo
import multiprocessing

import speedtest
import subprocess
import re
import numpy as np
import skfuzzy as fuzz
from skfuzzy import control as ctrl
from sklearn.metrics import precision_score, recall_score, f1_score
import json

app = FastAPI()

origins = [
    "http://localhost:3000",
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


class Metode(str, Enum):
    def __str__(self):
        return str(self.value)

    METODE = "playwrighthttpx"


# class Hasil(BaseModel):
#     keyword: str
#     pages: int
#     metode: Metode
#     upload: List[int]
#     download: List[int]
#     internet: List[int]
#     cpu_list: List[float]
#     ram_list: List[float]
#     time: float
#     cpu_type: Optional[str] = None
#     cpu_threads: Optional[int] = None
#     # cpu_frequency_max: Optional[float] = None
#     # cpu_max_percent: Optional[List] = None
#     ram_total: Optional[str] = None
#     ram_available: Optional[str] = None
#     # ram_max_percent: Optional[float] = None
#     jumlah_data: int
#     hasil: Optional[List[Dict]] = None


class DataRequest(BaseModel):
    keyword: str
    pages: int = Field(..., gt=0)
    metode: Metode

    @validator("keyword")
    def validate_keyword(cls, keyword):
        if not keyword.strip():
            raise ValueError("Keyword tidak boleh kosong atau hanya berisi spasi")
        return keyword


# data_playwrighthttpx = []


@app.post("/playwrighthttpx")
def input_playwrighthttpx(request: Request, input: DataRequest):
    try:
        headers = {"User-Agent": request.headers.get("User-Agent")}

        manager = multiprocessing.Manager()
        cpu_list = manager.list()  # List untuk menyimpan data pemantauan CPU
        ram_list = manager.list()  # List untuk menyimpan data pemantauan RAM
        upload_list = (
            manager.list()
        )  # List untuk menyimpan data pemantauan Internet Upload
        download_list = (
            manager.list()
        )  # List untuk menyimpan data pemantauan Internet Download
        internet_list = manager.list()  # List untuk menyimpan data pemantauan Internet

        monitor_process = multiprocessing.Process(
            target=monitoring,
            args=(cpu_list, ram_list, upload_list, download_list, internet_list),
        )
        monitor_process.start()

        start_time = time.time()
        hasil = asyncio.run(main(headers, input.keyword, input.pages))
        end_time = time.time()

        monitor_process.terminate()

        internet_upload = list(upload_list)
        internet_download = list(download_list)
        internet = list(internet_list)

        cpu_info = cpuinfo.get_cpu_info()
        cpu_type = cpu_info["brand_raw"]
        cpu_threads = psutil.cpu_count(logical=True)
        cpu_list_data = list(cpu_list)

        ram = psutil.virtual_memory()
        ram_total = format_bytes(ram.total)  # Total RAM dalam bytes
        ram_available = format_bytes(ram.available)  # RAM yang tersedia dalam bytes
        ram_list_data = list(ram_list)

        data = {
            "keyword": input.keyword,
            "pages": input.pages,
            "metode": input.metode,
            "upload": internet_upload,
            "download": internet_download,
            "internet": internet,
            "time": end_time - start_time,
            "cpu_list": cpu_list_data,
            "ram_list": ram_list_data,
            "cpu_type": cpu_type,
            "cpu_threads": cpu_threads,
            "ram_total": ram_total,
            "ram_available": ram_available,
            "jumlah_data": len(hasil),
            "hasil": hasil,
        }
        # data_playwrighthttpx.append(data)
        return data
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
            product_items = {}
            product_detail = {}

            for key, value in data_to_scrape.items():
                if key == "product_detail":
                    tag, attrs = value
                    elements = soup.find_all(tag, attrs)
                    if elements:
                        # results[key] = [element.text.strip() for element in elements]
                        for element in elements:
                            kunci = element.text.strip().split(":")[0]
                            product_detail[kunci] = element.text.strip().split(": ")[1]
                        results[key] = product_detail
                    else:
                        results[key] = None
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
                            product_items[kunci] = [
                                item.text.strip()
                                for item in items
                                if item.text.strip()
                                != ".css-1y1bj62{padding:4px 2px;display:-webkit-inline-box;display:-webkit-inline-flex;display:-ms-inline-flexbox;display:inline-flex;}"
                            ]
                        results[key] = product_items
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
                            separator="\n"
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


def monitoring(cpu_list, ram_list, upload_list, download_list, internet_list):
    # Memperoleh penggunaan internet
    last_total, last_sent, last_received = get_network_usage()
    while True:  # Loop utama untuk monitoring
        # Memperoleh penggunaan CPU
        cpu_usage = get_cpu_usage()
        print("Penggunaan CPU:", cpu_usage, "%")
        cpu_list.append(cpu_usage)

        # Memperoleh penggunaan RAM
        ram_usage = get_ram_usage()
        print("Penggunaan RAM:", ram_usage, "%")
        ram_list.append(ram_usage)

        # Memperoleh penggunaan internet
        bytes_total, bytes_sent, bytes_received = get_network_usage()

        # Menghitung penggunaan internet
        upload = bytes_sent - last_sent
        download = bytes_received - last_received
        internet = bytes_total - last_total

        print("Penggunaan Internet ~ Upload:", format_bytes(upload))
        print("Penggunaan Internet ~ Download:", format_bytes(download))
        print("Penggunaan Internet:", format_bytes(internet))
        upload_list.append(upload)
        download_list.append(download)
        internet_list.append(internet)


# Fungsi untuk memperoleh penggunaan CPU
def get_cpu_usage():
    return psutil.cpu_percent(interval=1, percpu=True)


# Fungsi untuk memperoleh penggunaan RAM
def get_ram_usage():
    ram = psutil.virtual_memory()
    return ram.percent


# Fungsi untuk memperoleh penggunaan Internet
def get_network_usage():
    received = psutil.net_io_counters().bytes_recv
    sent = psutil.net_io_counters().bytes_sent
    total = received + sent
    return total, sent, received


def format_bytes(bytes):
    # Fungsi ini mengubah ukuran byte menjadi format yang lebih mudah dibaca
    sizes = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while bytes >= 1024 and i < len(sizes) - 1:
        bytes /= 1024
        i += 1
    return "{:.2f} {}".format(bytes, sizes[i])


# @app.get("/data")
# def ambil_data(
#     keyword: Optional[str] = None,
#     pages: Optional[int] = None,
#     status: Optional[Status] = None,
# ):
#     if status is not None or keyword is not None or pages is not None:
#         result_filter = []
#         for data in data_playwrighthttpx:
#             data = Hasil.parse_obj(data)
#             if (
#                 status == data.status
#                 and data.keyword == keyword
#                 and data.pages == pages
#             ):
#                 result_filter.append(data)
#     else:
#         result_filter = data_playwrighthttpx
#     return result_filter


# @app.put("/monitoring")
# def ambil_data(input: DataRequest):
#     for data in data_playwrighthttpx:
#         if (
#             data["status"] == "baru"
#             and data["keyword"] == input.keyword
#             and data["pages"] == input.pages
#         ):
#             cpu_info = cpuinfo.get_cpu_info()
#             cpu_type = cpu_info["brand_raw"]
#             print("Tipe CPU:", cpu_type)
#             cpu_threads = psutil.cpu_count(logical=True)
#             print("thread cpu", cpu_threads)

#             ram = psutil.virtual_memory()
#             ram_total = ram.total  # Total RAM dalam bytes
#             print("Total RAM:", ram_total)
#             ram_available = ram.available  # RAM yang tersedia dalam bytes
#             print("RAM Tersedia:", ram_available)

#             cpu_percent_max = []  # Highest CPU usage during execution
#             cpu_frequency_max = 0  # Highest frekuensi CPU usage during execution
#             ram_percent_max = 0  # Highest RAM usage during execution

#             interval = 0.1  # Interval for monitoring (seconds)
#             duration = data["time"]
#             num_intervals = int(duration / interval) + 1

#             for _ in range(num_intervals):
#                 cpu_frequency = psutil.cpu_freq().current
#                 print("frekuensi cpu", cpu_frequency)
#                 cpu_percent = psutil.cpu_percent(interval=interval, percpu=True)
#                 print("cpu", cpu_percent)
#                 # Informasi RAM
#                 ram_percent = psutil.virtual_memory().percent
#                 print("ram", ram_percent)

#                 if cpu_frequency > cpu_frequency_max:
#                     cpu_frequency_max = cpu_frequency

#                 if cpu_percent > cpu_percent_max:
#                     cpu_percent_max = cpu_percent

#                 if ram_percent > ram_percent_max:
#                     ram_percent_max = ram_percent

#             data["cpu_type"] = cpu_type
#             data["cpu_threads"] = cpu_threads
#             data["cpu_frequency_max"] = cpu_frequency_max
#             data["cpu_max_percent"] = cpu_percent_max
#             data["ram_total"] = format_bytes(ram_total)
#             data["ram_max_percent"] = ram_percent_max
#             data["ram_available"] = format_bytes(ram_available)
#             data["status"] = "lama"

#     return data_playwrighthttpx


@app.get("/speed")
def cek_kecepatan_internet():
    try:
        st = speedtest.Speedtest()
        print("Mengukur kecepatan unduhan...")
        download_speed = (
            st.download() / 10**6
        )  # Mengukur kecepatan unduhan dalam Megabit per detik
        print("Kecepatan unduhan:", round(download_speed, 2), "Mbps")

        print("Mengukur kecepatan unggahan...")
        upload_speed = (
            st.upload() / 10**6
        )  # Mengukur kecepatan unggahan dalam Megabit per detik
        print("Kecepatan unggahan:", round(upload_speed, 2), "Mbps")

        # Pengecekan kestabilan koneksi
        print("Memeriksa kestabilan koneksi...")
        ping_packet_loss, ping_time_ms = get_ping()

        print("Waktu rata-rata ping ke www.tokopedia.com : ", ping_time_ms)
        if ping_packet_loss <= 1:
            print(f"ping_packet_loss: {ping_packet_loss} ~ Koneksi Stabil")
        else:
            print(f"ping_packet_loss: {ping_packet_loss} ~ Koneksi Tidak Stabil")

        # Penilaian jaringan berdasarkan metode Fuzzy Mamdani
        hasil = penilaian_jaringan(
            round(download_speed, 2),
            round(upload_speed, 2),
            ping_time_ms,
            ping_packet_loss,
        )
        penilaian = get_kondisi_penilaian(hasil)
        print(f"Penilaian mengunakan Metode Fuzzy Mamdani: {hasil} ~ {penilaian}")
        # uji_keakuratan(download_speed, upload_speed, ping, packet_loss, hasil, penilaian)

        data = {
            "speed_download": round(download_speed, 2),
            "speed_upload": round(upload_speed, 2),
            "ping_time_ms": ping_time_ms,
            "ping_packet_loss": ping_packet_loss,
            "nilai_fuzzy": hasil,
            "penilaian_fuzzy": penilaian,
        }
        return data

    except (Exception, speedtest.SpeedtestException) as e:
        print("Gagal melakukan pengujian kecepatan internet.", e)
        return e


def penilaian_jaringan(kecepatan_unduhan, kecepatan_unggahan, ping, packet_loss):
    # Membuat variabel linguistik
    kecepatan_unduhan_var = ctrl.Antecedent(np.arange(0, 101, 1), "kecepatan_unduhan")
    kecepatan_unggahan_var = ctrl.Antecedent(np.arange(0, 101, 1), "kecepatan_unggahan")
    ping_var = ctrl.Antecedent(np.arange(0, 101, 1), "ping")
    packet_loss_var = ctrl.Antecedent(np.arange(0, 101, 1), "packet_loss")
    penilaian = ctrl.Consequent(np.arange(0, 101, 1), "penilaian")

    # Mendefinisikan fungsi keanggotaan
    kecepatan_unduhan_var["lambat"] = fuzz.trimf(
        kecepatan_unduhan_var.universe, [0, 10, 20]
    )
    kecepatan_unduhan_var["sedang"] = fuzz.trimf(
        kecepatan_unduhan_var.universe, [15, 30, 50]
    )
    kecepatan_unduhan_var["cepat"] = fuzz.trimf(
        kecepatan_unduhan_var.universe, [40, 60, 100]
    )

    kecepatan_unggahan_var["lambat"] = fuzz.trimf(
        kecepatan_unggahan_var.universe, [0, 5, 15]
    )
    kecepatan_unggahan_var["sedang"] = fuzz.trimf(
        kecepatan_unggahan_var.universe, [10, 25, 50]
    )
    kecepatan_unggahan_var["cepat"] = fuzz.trimf(
        kecepatan_unggahan_var.universe, [35, 50, 100]
    )

    ping_var["cepat"] = fuzz.trimf(ping_var.universe, [0, 10, 30])
    ping_var["sedang"] = fuzz.trimf(ping_var.universe, [25, 30, 55])
    ping_var["lambat"] = fuzz.trimf(ping_var.universe, [50, 80, 100])

    packet_loss_var["rendah"] = fuzz.trimf(packet_loss_var.universe, [0, 10, 20])
    packet_loss_var["sedang"] = fuzz.trimf(packet_loss_var.universe, [10, 30, 50])
    packet_loss_var["tinggi"] = fuzz.trimf(packet_loss_var.universe, [40, 75, 100])

    penilaian["buruk"] = fuzz.trimf(penilaian.universe, [0, 10, 25])
    penilaian["cukup"] = fuzz.trimf(penilaian.universe, [20, 30, 50])
    penilaian["baik"] = fuzz.trimf(penilaian.universe, [40, 75, 100])

    # Membuat aturan fuzzy
    rule1 = ctrl.Rule(
        kecepatan_unduhan_var["lambat"] & kecepatan_unggahan_var["lambat"],
        penilaian["buruk"],
    )
    rule2 = ctrl.Rule(
        kecepatan_unduhan_var["lambat"] & kecepatan_unggahan_var["sedang"],
        penilaian["buruk"],
    )
    rule3 = ctrl.Rule(
        kecepatan_unduhan_var["sedang"] & kecepatan_unggahan_var["lambat"],
        penilaian["buruk"],
    )
    rule4 = ctrl.Rule(
        kecepatan_unduhan_var["sedang"] & kecepatan_unggahan_var["sedang"],
        penilaian["cukup"],
    )
    rule5 = ctrl.Rule(
        kecepatan_unduhan_var["cepat"] & kecepatan_unggahan_var["cepat"],
        penilaian["baik"],
    )
    rule6 = ctrl.Rule(
        kecepatan_unduhan_var["cepat"] & kecepatan_unggahan_var["sedang"],
        penilaian["baik"],
    )
    rule7 = ctrl.Rule(
        kecepatan_unduhan_var["sedang"] & kecepatan_unggahan_var["cepat"],
        penilaian["baik"],
    )
    rule8 = ctrl.Rule(
        kecepatan_unduhan_var["cepat"] & kecepatan_unggahan_var["lambat"],
        penilaian["cukup"],
    )
    rule9 = ctrl.Rule(
        kecepatan_unduhan_var["lambat"] & kecepatan_unggahan_var["cepat"],
        penilaian["cukup"],
    )

    rule10 = ctrl.Rule(
        kecepatan_unduhan_var["lambat"] & ping_var["lambat"], penilaian["buruk"]
    )
    rule11 = ctrl.Rule(
        kecepatan_unduhan_var["lambat"] & ping_var["sedang"], penilaian["buruk"]
    )
    rule12 = ctrl.Rule(
        kecepatan_unduhan_var["sedang"] & ping_var["lambat"], penilaian["buruk"]
    )
    rule13 = ctrl.Rule(
        kecepatan_unduhan_var["sedang"] & ping_var["sedang"], penilaian["cukup"]
    )
    rule14 = ctrl.Rule(
        kecepatan_unduhan_var["cepat"] & ping_var["cepat"], penilaian["baik"]
    )
    rule15 = ctrl.Rule(
        kecepatan_unduhan_var["cepat"] & ping_var["sedang"], penilaian["baik"]
    )
    rule16 = ctrl.Rule(
        kecepatan_unduhan_var["sedang"] & ping_var["cepat"], penilaian["baik"]
    )
    rule17 = ctrl.Rule(
        kecepatan_unduhan_var["cepat"] & ping_var["lambat"], penilaian["cukup"]
    )
    rule18 = ctrl.Rule(
        kecepatan_unduhan_var["lambat"] & ping_var["cepat"], penilaian["buruk"]
    )

    rule19 = ctrl.Rule(
        kecepatan_unduhan_var["lambat"] & packet_loss_var["tinggi"], penilaian["buruk"]
    )
    rule20 = ctrl.Rule(
        kecepatan_unduhan_var["lambat"] & packet_loss_var["sedang"], penilaian["buruk"]
    )
    rule21 = ctrl.Rule(
        kecepatan_unduhan_var["sedang"] & packet_loss_var["tinggi"], penilaian["cukup"]
    )
    rule22 = ctrl.Rule(
        kecepatan_unduhan_var["sedang"] & packet_loss_var["sedang"], penilaian["cukup"]
    )
    rule23 = ctrl.Rule(
        kecepatan_unduhan_var["cepat"] & packet_loss_var["rendah"], penilaian["baik"]
    )
    rule24 = ctrl.Rule(
        kecepatan_unduhan_var["cepat"] & packet_loss_var["sedang"], penilaian["cukup"]
    )
    rule25 = ctrl.Rule(
        kecepatan_unduhan_var["sedang"] & packet_loss_var["rendah"], penilaian["baik"]
    )
    rule26 = ctrl.Rule(
        kecepatan_unduhan_var["cepat"] & packet_loss_var["tinggi"], penilaian["cukup"]
    )
    rule27 = ctrl.Rule(
        kecepatan_unduhan_var["lambat"] & packet_loss_var["rendah"], penilaian["buruk"]
    )

    rule28 = ctrl.Rule(
        kecepatan_unggahan_var["lambat"] & ping_var["lambat"], penilaian["buruk"]
    )
    rule29 = ctrl.Rule(
        kecepatan_unggahan_var["lambat"] & ping_var["sedang"], penilaian["buruk"]
    )
    rule30 = ctrl.Rule(
        kecepatan_unggahan_var["sedang"] & ping_var["lambat"], penilaian["cukup"]
    )
    rule31 = ctrl.Rule(
        kecepatan_unggahan_var["sedang"] & ping_var["sedang"], penilaian["cukup"]
    )
    rule32 = ctrl.Rule(
        kecepatan_unggahan_var["cepat"] & ping_var["cepat"], penilaian["baik"]
    )
    rule33 = ctrl.Rule(
        kecepatan_unggahan_var["cepat"] & ping_var["sedang"], penilaian["cukup"]
    )
    rule34 = ctrl.Rule(
        kecepatan_unggahan_var["sedang"] & ping_var["cepat"], penilaian["baik"]
    )
    rule35 = ctrl.Rule(
        kecepatan_unggahan_var["cepat"] & ping_var["lambat"], penilaian["cukup"]
    )
    rule36 = ctrl.Rule(
        kecepatan_unggahan_var["lambat"] & ping_var["cepat"], penilaian["buruk"]
    )

    rule37 = ctrl.Rule(
        kecepatan_unggahan_var["lambat"] & packet_loss_var["tinggi"], penilaian["buruk"]
    )
    rule38 = ctrl.Rule(
        kecepatan_unggahan_var["lambat"] & packet_loss_var["sedang"], penilaian["buruk"]
    )
    rule39 = ctrl.Rule(
        kecepatan_unggahan_var["sedang"] & packet_loss_var["tinggi"], penilaian["cukup"]
    )
    rule40 = ctrl.Rule(
        kecepatan_unggahan_var["sedang"] & packet_loss_var["sedang"], penilaian["cukup"]
    )
    rule41 = ctrl.Rule(
        kecepatan_unggahan_var["cepat"] & packet_loss_var["rendah"], penilaian["baik"]
    )
    rule42 = ctrl.Rule(
        kecepatan_unggahan_var["cepat"] & packet_loss_var["sedang"], penilaian["cukup"]
    )
    rule43 = ctrl.Rule(
        kecepatan_unggahan_var["sedang"] & packet_loss_var["rendah"], penilaian["baik"]
    )
    rule44 = ctrl.Rule(
        kecepatan_unggahan_var["cepat"] & packet_loss_var["tinggi"], penilaian["cukup"]
    )
    rule45 = ctrl.Rule(
        kecepatan_unggahan_var["lambat"] & packet_loss_var["rendah"], penilaian["buruk"]
    )

    rule46 = ctrl.Rule(
        ping_var["lambat"] & packet_loss_var["tinggi"], penilaian["buruk"]
    )
    rule47 = ctrl.Rule(
        ping_var["lambat"] & packet_loss_var["sedang"], penilaian["buruk"]
    )
    rule48 = ctrl.Rule(
        ping_var["sedang"] & packet_loss_var["tinggi"], penilaian["cukup"]
    )
    rule49 = ctrl.Rule(
        ping_var["sedang"] & packet_loss_var["sedang"], penilaian["cukup"]
    )
    rule50 = ctrl.Rule(ping_var["cepat"] & packet_loss_var["rendah"], penilaian["baik"])
    rule51 = ctrl.Rule(
        ping_var["cepat"] & packet_loss_var["sedang"], penilaian["cukup"]
    )
    rule52 = ctrl.Rule(
        ping_var["sedang"] & packet_loss_var["rendah"], penilaian["cukup"]
    )
    rule53 = ctrl.Rule(
        ping_var["cepat"] & packet_loss_var["tinggi"], penilaian["buruk"]
    )
    rule54 = ctrl.Rule(
        ping_var["lambat"] & packet_loss_var["rendah"], penilaian["buruk"]
    )

    # Membuat sistem kontrol
    penilaian_ctrl = ctrl.ControlSystem(
        [
            rule1,
            rule2,
            rule3,
            rule4,
            rule5,
            rule6,
            rule7,
            rule8,
            rule9,
            rule10,
            rule11,
            rule12,
            rule13,
            rule14,
            rule15,
            rule16,
            rule17,
            rule18,
            rule19,
            rule20,
            rule21,
            rule22,
            rule23,
            rule24,
            rule25,
            rule26,
            rule27,
            rule28,
            rule29,
            rule30,
            rule31,
            rule32,
            rule33,
            rule34,
            rule35,
            rule36,
            rule37,
            rule38,
            rule39,
            rule40,
            rule41,
            rule42,
            rule43,
            rule44,
            rule45,
            rule46,
            rule47,
            rule48,
            rule49,
            rule50,
            rule51,
            rule52,
            rule53,
            rule54,
        ]
    )
    penilaian = ctrl.ControlSystemSimulation(penilaian_ctrl)

    # Menetapkan input ke sistem kontrol fuzzy
    penilaian.input["kecepatan_unduhan"] = kecepatan_unduhan
    penilaian.input["kecepatan_unggahan"] = kecepatan_unggahan
    penilaian.input["ping"] = ping
    penilaian.input["packet_loss"] = packet_loss

    # Melakukan proses inferensi fuzzy
    penilaian.compute()

    # Mengembalikan hasil penilaian
    return penilaian.output["penilaian"]


def get_kondisi_penilaian(penilaian):
    if penilaian <= 25:
        kondisi = "lambat"
    elif penilaian > 25 and penilaian <= 50:
        kondisi = "sedang"
    else:
        kondisi = "cepat"
    return kondisi


def get_ping():
    try:
        process = subprocess.Popen(
            ["ping", "-c", "10", "www.tokopedia.com"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        output, error = process.communicate()
        error_str = error.decode("utf-8")
        output_str = output.decode("utf-8")
        packet_loss = extract_packet_loss(output_str)
        average_time = extract_average_time(output_str)

        # Menampilkan proses pengecekan koneksi
        print("\n=== Pengecekan Kestabilan Koneksi ===")
        print(error_str)
        print("----------------------------------------")
        print(output_str)

        return packet_loss, average_time
    except subprocess.CalledProcessError:
        return 100.0, 1000.0  # Jika terjadi kesalahan, asumsikan 100% packet loss


def extract_packet_loss(output):
    match = re.search(r"(\d+(\.\d+)?)% packet loss", output)
    if match:
        packet_loss = float(match.group(1))
        return packet_loss


def extract_average_time(output):
    times = re.findall(r"time=([\d.]+) ms", output)
    if times:
        time = [float(time) for time in times]
        average_time = sum(time) / len(time)
        return average_time


# def uji_keakuratan(kecepatan_unduhan, kecepatan_unggahan, ping, packet_loss, hasil_fuzzy, penilaian):
@app.get("/uji")
def uji_keakuratan():
    try:
        # Path file JSON
        file_path = "./data_pengujian.json"

        with open(file_path, "r") as json_file:
            test_data = json.load(json_file)

        total_data = len(test_data)
        # benar = 0

        hasil = []

        y_true = []  # Target yang sebenarnya
        y_pred = []  # Target yang diprediksi

        for data in test_data:
            target_kecepatan_unduhan = data["kecepatan_unduhan"]
            target_kecepatan_unggahan = data["kecepatan_unggahan"]
            target_ping_time_ms = data["ping_time_ms"]
            target_ping_packet_loss = data["ping_packet_loss"]
            # target_hasil = data["fuzzy"]
            target_penilaian = data["penilaian"]

            hasil_uji = penilaian_jaringan(
                target_kecepatan_unduhan,
                target_kecepatan_unggahan,
                target_ping_time_ms,
                target_ping_packet_loss,
            )
            penilaian_uji = get_kondisi_penilaian(hasil_uji)
            # Mengambil keputusan berdasarkan hasil penilaian

            # threshold = 1.0  # Set the threshold value for classification
            # if target_hasil > hasil_uji:
            #     hasil_uji = min(target_hasil, hasil_uji)
            # if abs(hasil_uji - target_hasil) <= threshold:
            #     benar += 1
            #     print(
            #         f"Area threshold: {benar}). data uji: {target_hasil} ~ hasil pengujian: {hasil_uji}"
            #     )

            # Menambahkan target sebenarnya dan target prediksi ke dalam list
            y_true.append(target_penilaian)
            y_pred.append(penilaian_uji)

            # Menampilkan hasil pengujian
            print("----------------------------------------")
            print("Data Pengujian:")
            print("Kecepatan Unduhan:", target_kecepatan_unduhan)
            print("Kecepatan Unggahan:", target_kecepatan_unggahan)
            print("ping time ms:", target_ping_time_ms)
            print("Ping Packet Loss:", target_ping_packet_loss)
            # print("Hasil_fuzzy:", target_hasil)
            print("Penilaian:", target_penilaian)
            print("----------------------------------------")
            print("Hasil Pengujian fuzzy:", hasil_uji)
            print("Penilaian Pengujian:", penilaian_uji)
            print("----------------------------------------")
            data_uji = {
                "speed_download": target_kecepatan_unduhan,
                "speed_upload": target_kecepatan_unggahan,
                "ping_time_ms": target_ping_time_ms,
                "ping_packet_loss": target_ping_packet_loss,
                # "hasil": target_hasil,
                "penilaian": target_penilaian,
                "hasil_fuzzy": hasil_uji,
                "penilaian_fuzzy": penilaian_uji,
            }
            hasil.append(data_uji)

        # Menghitung keakuratan
        print("Jumlah data uji: ", total_data)
        # print("Selisih nilai yang di tolerir: ", threshold)
        # keakuratan = benar / total_data * 100
        # print(
        #     "Keakuratan data uji dengan hasil Fuzzy Mamdani: {:.2f}%".format(keakuratan)
        # )

        # Menghitung precision, recall, dan F1-score
        precision = precision_score(y_true, y_pred, average="weighted", zero_division=1)
        recall = recall_score(y_true, y_pred, average="weighted", zero_division=1)
        f1 = f1_score(y_true, y_pred, average="weighted", zero_division=1)

        # Menampilkan hasil pengujian akurasi
        print("Precision: {:.2f}".format(precision))
        print("Recall: {:.2f}".format(recall))
        print("F1-score: {:.2f}".format(f1))

        data = {
            "total_data": total_data,
            # "threshold": threshold,
            # "keakuratan": keakuratan,
            "precision": precision,
            "recall": recall,
            "f1 score": f1,
            "hasil": hasil,
        }
        return data
    except Exception as e:
        print(e)
        return e
