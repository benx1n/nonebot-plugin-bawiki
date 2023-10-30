from datetime import datetime, timedelta
from enum import Enum, auto
from functools import partial
from io import BytesIO
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    NamedTuple,
    Optional,
    Sequence,
    TypedDict,
    TypeVar,
    Union,
    cast,
)
from typing_extensions import Unpack

from async_lru import _LRUCacheWrapper
from httpx import AsyncClient
from nonebot import logger
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from PIL import Image, ImageOps
from pil_utils import BuildImage

from .config import config

T = TypeVar("T")
TC = TypeVar("TC", bound=Callable)
NestedIterable = Iterable[Union[T, Iterable["NestedIterable[T]"]]]


def wrapped_alru_cache(
    maxsize: Optional[int] = None,
    typed: bool = False,
    ttl: Optional[int] = None,
):
    def wrapper(func: TC) -> TC:
        # 为了让 lru cache 兼容 dict 只能这样了...
        # 写你妈 type hint，我累了

        def convert_dict(obj):
            return frozenset(obj.items()) if isinstance(obj, dict) else obj

        def recover_dict(obj):
            return dict(obj) if isinstance(obj, frozenset) else obj

        def process_args(func, args, kwargs):
            new_args = tuple(func(arg) for arg in args)
            new_kwargs = {}
            for k, v in kwargs.items():
                new_kwargs[k] = func(v)
            return new_args, new_kwargs

        class CacheWrapper(_LRUCacheWrapper):  # type: ignore
            async def __call__(self, *args, **kwargs):
                new_args, new_kwargs = process_args(convert_dict, args, kwargs)
                return await super().__call__(*new_args, **new_kwargs)

        async def wrapped_func(*args, **kwargs):
            new_args, new_kwargs = process_args(recover_dict, args, kwargs)
            return await func(*new_args, **new_kwargs)

        return cast(Any, CacheWrapper(wrapped_func, maxsize, typed, ttl))

    return wrapper


class RespType(Enum):
    JSON = auto()
    TEXT = auto()
    BYTES = auto()


class AsyncReqKwargs(TypedDict, total=False):
    method: str
    params: Optional[Dict[Any, Any]]
    headers: Optional[Dict[str, Any]]
    content: Union[str, bytes, None]
    data: Optional[Dict[str, Any]]
    json: Optional[Any]
    base_url: str
    proxies: Optional[str]

    resp_type: RespType
    retries: int
    raise_for_status: bool


@wrapped_alru_cache(ttl=config.ba_req_cache_ttl, maxsize=None)
async def async_req(*urls: str, **kwargs: Unpack[AsyncReqKwargs]) -> Any:
    if not urls:
        raise ValueError("No URL specified")

    method = kwargs.pop("method", "GET")
    params = kwargs.pop("params", None)
    headers = kwargs.pop("headers", None)
    content = kwargs.pop("content", None)
    data = kwargs.pop("data", None)
    json = kwargs.pop("json", None)

    base_url = kwargs.pop("base_url", "")
    proxies = kwargs.pop("proxies", config.ba_proxy)

    resp_type = kwargs.pop("resp_type", RespType.JSON)
    retries = kwargs.pop("retries", config.ba_req_retry)
    raise_for_status = kwargs.pop("raise_for_status", True)

    url, rest = urls[0], urls[1:]
    try:
        async with AsyncClient(
            base_url=base_url,
            proxies=proxies,
            follow_redirects=True,
            timeout=config.ba_req_timeout,
        ) as cli:
            resp = await cli.request(
                method,
                url,
                params=params,
                headers=headers,
                content=content,
                data=data,
                json=json,
            )
            if raise_for_status:
                resp.raise_for_status()

            if method.upper() == "HEAD":
                return resp.headers
            if resp_type == RespType.JSON:
                return resp.json()
            if resp_type == RespType.TEXT:
                return resp.text
            return resp.content

    except Exception as e:
        recursive_func = partial(async_req, **kwargs)

        if retries <= 0:
            if not rest:
                raise

            logger.error(
                f"Requesting next url because error occurred while requesting {url}: {e!r}",
            )
            logger.opt(exception=e).debug("Error Stack")
            return await recursive_func(*rest)

        retries -= 1
        logger.error(
            f"Retrying ({retries} left) because error occurred while requesting {url}: {e!r}",
        )
        logger.opt(exception=e).debug("Error Stack")
        return await recursive_func(*urls)


def clear_req_cache() -> int:
    lru_wrapper = cast(_LRUCacheWrapper[Any], async_req)
    info = lru_wrapper.cache_info()
    lru_wrapper.cache_clear()
    return info.currsize


def format_timestamp(t: int) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def recover_alia(origin: str, alia_dict: Dict[str, List[str]]):
    origin = replace_brackets(origin).strip()
    origin_ = origin.lower()

    # 精确匹配
    for k, li in alia_dict.items():
        if origin_ in li or origin_ == k:
            return k

    # 没找到，模糊匹配
    origin_ = origin.replace(" ", "")
    for k, li in alia_dict.items():
        li = [x.replace(" ", "") for x in ([k, *li])]
        for v in li:
            if origin_ in v:
                return k

    return origin


class ParsedTimeDelta(NamedTuple):
    days: int
    hours: int
    minutes: int
    seconds: int


def parse_time_delta(t: timedelta) -> ParsedTimeDelta:
    mm, ss = divmod(t.seconds, 60)
    hh, mm = divmod(mm, 60)
    dd = t.days or 0
    return ParsedTimeDelta(dd, hh, mm, ss)


def img_invert_rgba(im: Image.Image) -> Image.Image:
    # https://stackoverflow.com/questions/2498875/how-to-invert-colors-of-image-with-pil-python-imaging
    r, g, b, a = im.split()
    rgb_image = Image.merge("RGB", (r, g, b))
    inverted_image = ImageOps.invert(rgb_image)
    r2, g2, b2 = inverted_image.split()
    return Image.merge("RGBA", (r2, g2, b2, a))


def replace_brackets(original: str) -> str:
    return original.replace("（", "(").replace("）", "(")


def splice_msg(msgs: Sequence[Union[str, MessageSegment, Message]]) -> Message:
    im = Message()
    for i, v in enumerate(msgs):
        if isinstance(v, str) and (i != 0):
            v = f"\n{v}"
        im += v
    return im


def split_list(lst: Sequence[T], n: int) -> Iterator[Sequence[T]]:
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def split_pic(pic: Image.Image, max_height: int = 4096) -> List[Image.Image]:
    pw, ph = pic.size
    if ph <= max_height:
        return [pic]

    ret = []
    need_merge_last = ph % max_height < max_height // 2
    iter_times = ph // max_height

    now_h = 0
    for i in range(iter_times):
        if i == iter_times - 1 and need_merge_last:
            ret.append(pic.crop((0, now_h, pw, ph)))
            break

        ret.append(pic.crop((0, now_h, pw, now_h + max_height)))
        now_h += max_height

    return ret


def i2b(image: Image.Image, img_format: str = "JPEG") -> BytesIO:
    buf = BytesIO()
    image.save(buf, img_format)
    buf.seek(0)
    return buf


def read_image(path: Path) -> BuildImage:
    content = path.read_bytes()
    bio = BytesIO(content)
    return BuildImage.open(bio)


async def send_forward_msg(
    bot: Bot,
    event: MessageEvent,
    messages: Sequence[Union[str, MessageSegment, Message]],
    user_id: Optional[int] = None,
    nickname: Optional[str] = None,
):
    nodes: List[MessageSegment] = [
        MessageSegment.node_custom(
            int(bot.self_id) if user_id is None else user_id,
            "BAWiki" if nickname is None else nickname,
            Message(x),
        )
        for x in messages
    ]
    if isinstance(event, GroupMessageEvent):
        return await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
    return await bot.send_private_forward_msg(user_id=event.user_id, messages=nodes)
