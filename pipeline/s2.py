"""Загрузка Sentinel-2 L2A из Microsoft Planetary Computer (STAC, без ключа).

Каждая акватория получает фиксированную сетку 10 м в своей зоне UTM, поэтому
все даты одной акватории совпадают попиксельно.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
import planetary_computer as pc
import rasterio
from pystac_client import Client
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from shapely.geometry import box, shape

from .config import AOIS, BANDS, PROCESSED

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
    GDAL_HTTP_MAX_RETRY="4",
    GDAL_HTTP_RETRY_DELAY="1",
    VSI_CACHE="TRUE",
)


@dataclass
class Grid:
    crs: CRS
    transform: rasterio.Affine
    width: int
    height: int

    @property
    def bounds(self):
        t = self.transform
        return (t.c, t.f + t.e * self.height, t.c + t.a * self.width, t.f)


def aoi_grid(aoi_id: str, res: float = 10.0) -> Grid:
    lon0, lat0, lon1, lat1 = AOIS[aoi_id]["bbox"]
    zone = int(((lon0 + lon1) / 2 + 180) // 6) + 1
    epsg = (32600 if (lat0 + lat1) / 2 >= 0 else 32700) + zone
    crs = CRS.from_epsg(epsg)
    x0, y0, x1, y1 = transform_bounds("EPSG:4326", crs, lon0, lat0, lon1, lat1)
    x0, y1 = np.floor(x0 / res) * res, np.ceil(y1 / res) * res
    w, h = int(np.ceil((x1 - x0) / res)), int(np.ceil((y1 - y0) / res))
    return Grid(crs, from_origin(x0, y1, res, res), w, h)


def search(aoi_id: str, start: str, end: str, max_cloud: float = 30) -> list:
    """Сцены, почти полностью покрывающие акваторию, по одной на дату (лучшее покрытие)."""
    bbox = AOIS[aoi_id]["bbox"]
    aoi = box(*bbox)
    client = Client.open(STAC_URL, modifier=pc.sign_inplace)
    items = client.search(
        collections=["sentinel-2-l2a"], bbox=bbox, datetime=f"{start}/{end}",
        query={"eo:cloud_cover": {"lt": max_cloud}},
    ).item_collection()
    best = {}
    for it in items:
        cov = shape(it.geometry).intersection(aoi).area / aoi.area
        if cov < 0.97:
            continue
        date = it.datetime.strftime("%Y-%m-%d")
        if date not in best or cov > best[date][0]:
            best[date] = (cov, it)
    return [best[d][1] for d in sorted(best)]


def _offset(item) -> float:
    # С базовой линии обработки 04.00 (янв 2022) в L2A добавлен сдвиг BOA_ADD_OFFSET = -1000
    pb = float(item.properties.get("s2:processing_baseline", "0") or 0)
    return -1000.0 if pb >= 4.0 else 0.0


def _read(href: str, grid: Grid, resampling: Resampling) -> np.ndarray:
    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as src:
        with WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width,
                       height=grid.height, resampling=resampling, src_nodata=0, nodata=0) as vrt:
            return vrt.read(1)


def load_scene(item, grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """Возвращает (reflectance float32 [11,H,W], SCL uint8 [H,W])."""
    off = _offset(item)
    jobs = [(item.assets[b].href, Resampling.bilinear) for b in BANDS]
    jobs.append((item.assets["SCL"].href, Resampling.nearest))
    with ThreadPoolExecutor(max_workers=6) as ex:
        arrs = list(ex.map(lambda j: _read(j[0], grid, j[1]), jobs))
    dn = np.stack(arrs[:-1]).astype(np.float32)
    nodata = dn[1] == 0
    refl = (dn + off) / 10000.0
    refl[:, nodata] = np.nan
    return refl, arrs[-1].astype(np.uint8)


def save_scene(aoi_id: str, item, grid: Grid, refl: np.ndarray, scl: np.ndarray) -> None:
    date = item.datetime.strftime("%Y-%m-%d")
    out = PROCESSED / aoi_id / date
    out.mkdir(parents=True, exist_ok=True)
    prof = dict(driver="GTiff", crs=grid.crs, transform=grid.transform, width=grid.width,
                height=grid.height, compress="deflate", tiled=True, predictor=2)
    dn = np.nan_to_num(refl * 10000, nan=-9999).round().astype(np.int16)
    with rasterio.open(out / "bands.tif", "w", count=dn.shape[0], dtype="int16", nodata=-9999, **prof) as dst:
        dst.write(dn)
    with rasterio.open(out / "scl.tif", "w", count=1, dtype="uint8", **prof) as dst:
        dst.write(scl, 1)
    meta = {
        "id": item.id, "date": date, "datetime": item.datetime.isoformat(),
        "cloud_cover": item.properties.get("eo:cloud_cover"),
        "platform": item.properties.get("platform"),
        "tile": item.properties.get("s2:mgrs_tile"),
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def load_saved(aoi_id: str, date: str):
    d = PROCESSED / aoi_id / date
    with rasterio.open(d / "bands.tif") as src:
        dn = src.read().astype(np.float32)
        prof = src.profile
    refl = np.where(dn == -9999, np.nan, dn / 10000.0).astype(np.float32)
    with rasterio.open(d / "scl.tif") as src:
        scl = src.read(1)
    return refl, scl, prof
