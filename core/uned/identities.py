"""
UNED 身份信息管理组件
从 F:\\github\\资料 目录解析并缓存真实身份证与个人信息
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("uned.identities")

_DEFAULT_DIR = Path("F:/github/资料")
_CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "uned_identities.json"

# 常见拼音姓氏/名字映射，用于生成西语规范的姓名拼音
_PINYIN_MAP = {
    "张": "Zhang", "李": "Li", "王": "Wang", "刘": "Liu", "陈": "Chen",
    "杨": "Yang", "黄": "Huang", "赵": "Zhao", "吴": "Wu", "周": "Zhou",
    "徐": "Xu", "孙": "Sun", "马": "Ma", "朱": "Zhu", "胡": "Hu",
    "郭": "Guo", "何": "He", "高": "Gao", "林": "Lin", "罗": "Luo",
    "郑": "Zheng", "梁": "Liang", "谢": "Xie", "宋": "Song", "唐": "Tang",
    "许": "Xu", "韩": "Han", "冯": "Feng", "邓": "Deng", "曹": "Cao",
    "彭": "Peng", "曾": "Zeng", "肖": "Xiao", "田": "Tian", "董": "Dong",
    "袁": "Yuan", "潘": "Pan", "于": "Yu", "蒋": "Jiang", "蔡": "Cai",
    "余": "Yu", "杜": "Du", "叶": "Ye", "程": "Cheng", "苏": "Su",
    "魏": "Wei", "吕": "Lv", "丁": "Ding", "任": "Ren", "沈": "Shen",
    "姚": "Yao", "卢": "Lu", "姜": "Jiang", "崔": "Cui", "钟": "Zhong",
    "谭": "Tan", "陆": "Lu", "汪": "Wang", "范": "Fan", "金": "Jin",
    "石": "Shi", "廖": "Liao", "贾": "Jia", "夏": "Xia", "韦": "Wei",
    "付": "Fu", "方": "Fang", "白": "Bai", "邹": "Zou", "孟": "Meng",
    "熊": "Xiong", "秦": "Qin", "邱": "Qiu", "江": "Jiang", "尹": "Yin",
    "薛": "Xue", "闫": "Yan", "段": "Duan", "雷": "Lei", "侯": "Hou",
    "龙": "Long", "史": "Shi", "陶": "Tao", "黎": "Li", "贺": "He",
    "顾": "Gu", "毛": "Mao", "郝": "Hao", "龚": "Gong", "邵": "Shao",
    "万": "Wan", "钱": "Qian", "严": "Yan", "覃": "Qin", "武": "Wu",
    "戴": "Dai", "莫": "Mo", "孔": "Kong", "向": "Xiang", "汤": "Tang"
}

_GIVEN_NAME_MAP = {
    "磊": "Lei", "伟": "Wei", "强": "Qiang", "军": "Jun", "平": "Ping",
    "明": "Ming", "杰": "Jie", "芳": "Fang", "娜": "Na", "敏": "Min",
    "静": "Jing", "丽": "Li", "娟": "Juan", "艳": "Yan", "霞": "Xia",
    "秀英": "Xiuying", "建华": "Jianhua", "浩": "Hao", "鹏": "Peng",
    "欣": "Xin", "宇": "Yu", "博": "Bo", "涵": "Han", "晨": "Chen",
    "超": "Chao", "洋": "Yang", "勇": "Yong", "涛": "Tao", "刚": "Gang"
}

def _to_pinyin_name(raw_name: str) -> tuple[str, str, str]:
    """返回 (名字拼音, 第一姓氏拼音, 第二姓氏拼音)"""
    if not raw_name:
        return ("Lei", "Zhang", "Zhang")
    
    first_char = raw_name[0]
    surname = _PINYIN_MAP.get(first_char, "Zhang")
    
    rest = raw_name[1:]
    if rest in _GIVEN_NAME_MAP:
        given = _GIVEN_NAME_MAP[rest]
    elif len(rest) == 1 and rest in _GIVEN_NAME_MAP:
        given = _GIVEN_NAME_MAP[rest]
    else:
        # 默认备用拼音
        given = "Lei" if surname != "Lei" else "Ming"
    
    return (given, surname, surname)

def load_or_scan_identities(data_dir: Optional[Path] = None, force_rescan: bool = False) -> list[dict]:
    """加载或扫描 F:\\github\\资料 目录下的所有身份证"""
    if not force_rescan and _CACHE_FILE.exists():
        try:
            cached = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if cached and isinstance(cached, list):
                return cached
        except Exception:
            pass

    scan_dir = data_dir or _DEFAULT_DIR
    if not scan_dir.exists():
        logger.warning(f"身份资料目录不存在: {scan_dir}")
        return []

    from core.uned.captcha_solver import get_ocr_instance
    ocr = get_ocr_instance()
    
    identities = []
    seen_docs = set()

    # 先载入已有缓存（防止丢失历史身份证对应的姓名与拼音信息）
    if _CACHE_FILE.exists():
        try:
            old_cached = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            for item in old_cached:
                doc = item.get("doc_number")
                if doc and doc not in seen_docs:
                    identities.append(item)
                    seen_docs.add(doc)
        except Exception:
            pass

    # 递归扫描目录及子目录（兼容用户整理的"已用"子文件夹与根目录新加素材）
    image_paths = sorted(list(scan_dir.rglob("*.png")) + list(scan_dir.rglob("*.jpg")) + list(scan_dir.rglob("*.jpeg")))
    for p in image_paths:
        try:
            res, _ = ocr(str(p))
            if not res:
                continue
            lines = [x[1] for x in res]
            text = " ".join(lines)
            
            # 正则匹配姓名与18位身份证号
            name_m = re.search(r'姓\s*名\s*([^\s\d]{2,4})', text)
            id_m = re.search(r'([1-8]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx])', text)
            
            raw_name = name_m.group(1) if name_m else "张磊"
            gender = "M" if "男" in text else "F"
            
            if id_m:
                id_num = id_m.group(1).upper()
                if id_num in seen_docs:
                    continue
                seen_docs.add(id_num)

                day = id_num[12:14]
                month = id_num[10:12]
                year = id_num[6:10]
                dob = f"{day}/{month}/{year}"
                given, sn1, sn2 = _to_pinyin_name(raw_name)
                
                identities.append({
                    "file": p.name,
                    "raw_name": raw_name,
                    "name": given,
                    "surname1": sn1,
                    "surname2": sn2,
                    "gender": gender,
                    "doc_number": id_num,
                    "dob": dob,
                    "nationality": "RPC",
                    "doc_type_val": "05",
                    "country_val": "RPC"
                })
        except Exception as e:
            logger.warning(f"解析证件图片 {p.name} 失败: {e}")

    if identities:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(identities, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"成功扫描并合并缓存 {len(identities)} 张身份证信息至 {_CACHE_FILE}")

    return identities

_FAILED_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "uned_failed_identities.json"

def get_failed_identities() -> dict[str, str]:
    """获取所有已知注册失败/失效的身份证号码及原因"""
    if _FAILED_FILE.exists():
        try:
            return json.loads(_FAILED_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def mark_failed_identity(doc_number: str, reason: str = ""):
    """标记某个身份证号为不可用（如在 UNED 已被注册或证件号被拒）"""
    failed = get_failed_identities()
    failed[doc_number] = reason or "UNED rejected or duplicate"
    try:
        _FAILED_FILE.parent.mkdir(parents=True, exist_ok=True)
        _FAILED_FILE.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"已将证件 {doc_number} 标记为不可用: {reason}")
    except Exception as e:
        logger.warning(f"记录失败证件异常: {e}")

def pick_identity(used_doc_numbers: Optional[set] = None, exclude_failed: bool = True) -> dict:
    """获取下一个未使用的身份证信息"""
    all_ids = load_or_scan_identities()
    if not all_ids:
        # 回退默认身份信息
        return {
            "name": "Lei",
            "surname1": "Zhang",
            "surname2": "Zhang",
            "gender": "M",
            "doc_number": "610431200602061916",
            "dob": "06/02/2006",
            "nationality": "RPC",
            "doc_type_val": "05",
            "country_val": "RPC"
        }
    
    used = set(used_doc_numbers or set())
    if exclude_failed:
        failed = get_failed_identities()
        used.update(failed.keys())

    for item in all_ids:
        doc = item.get("doc_number")
        if doc and doc not in used:
            return item
            
    # 如果所有扫描到的真实身份证均已注册或标记失败
    raise RuntimeError(f"所有有效身份证已全部使用完毕 (总共 {len(all_ids)} 张，已用/排查: {len(used)})")

