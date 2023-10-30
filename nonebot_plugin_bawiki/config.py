from typing import Optional

from nonebot import get_driver
from pydantic import BaseModel, Field, HttpUrl


class Cfg(BaseModel):
    ba_proxy: Optional[str] = None
    ba_gacha_cool_down: int = 0
    ba_voice_use_card: bool = False
    ba_use_forward_msg: bool = True
    ba_screenshot_timeout: int = 60
    ba_disable_classic_gacha: bool = False
    ba_gacha_max: int = 200

    ba_gamekee_url: HttpUrl = Field("https://ba.gamekee.com/")
    ba_schale_url: HttpUrl = Field("https://schale.gg/")
    ba_bawiki_db_url: HttpUrl = Field("https://bawiki.lgc2333.top/")
    ba_arona_api_url: HttpUrl = Field("https://arona.diyigemt.com/")
    ba_arona_cdn_url: HttpUrl = Field("https://arona.cdn.diyigemt.com/")
    ba_shittim_chest_api_url: HttpUrl = Field("https://api.ba.benx1n.com/")
    ba_shittim_chest_data_url: HttpUrl = Field("https://data.ba.benx1n.com/")

    ba_req_retry: int = 1
    ba_req_cache_ttl: int = 10800  # 3 hrs
    ba_req_timeout: Optional[float] = 10.0
    ba_auto_clear_arona_cache: bool = False


config = Cfg.parse_obj(get_driver().config.dict())
