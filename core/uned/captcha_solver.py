"""
UNED 验证码自动化识别组件 (基于本地轻量 RapidOCR ONNX + 图像超分辨率预处理)
完全离线、低内存、零外部网络依赖，单次推理 <0.3s
"""

import io
import re
from pathlib import Path
from typing import Union, Optional

from PIL import Image, ImageEnhance

_ocr = None

def get_ocr_instance():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr

def preprocess_captcha(image_input: Union[str, Path, bytes]) -> Image.Image:
    """对小型噪点验证码进行 3 倍超分放大与对比度增强"""
    if isinstance(image_input, (str, Path)):
        img = Image.open(str(image_input)).convert("RGB")
    elif isinstance(image_input, bytes):
        img = Image.open(io.BytesIO(image_input)).convert("RGB")
    else:
        img = image_input.convert("RGB")

    w, h = img.size
    # 放大 3 倍使 ONNX 检测网络能清晰分辨笔画
    scaled = img.resize((w * 3, h * 3), Image.Resampling.LANCZOS)
    # 增强对比度，抑制微弱噪点
    enhancer = ImageEnhance.Contrast(scaled)
    enhanced = enhancer.enhance(1.8)
    return enhanced

def solve_uned_captcha(image_input: Union[str, Path, bytes]) -> Optional[str]:
    """
    识别 UNED 首页 5 位字符图形验证码。
    严格返回 5 位字母数字字符串；若识别结果不足 5 位则返回 None（建议直接刷新，避免盲猜被拒）。
    """
    ocr = get_ocr_instance()
    
    # 1. 优先使用增强图像直接送入识别网络 (跳过检测网络分割，大幅提升验证码字符完整度与准确率)
    try:
        prep_img = preprocess_captcha(image_input)
        img_byte_arr = io.BytesIO()
        prep_img.save(img_byte_arr, format='PNG')
        prep_bytes = img_byte_arr.getvalue()
        
        result_rec, _ = ocr(prep_bytes, use_det=False)
        if result_rec:
            raw_text = "".join(x[0] for x in result_rec)
            cleaned = "".join(re.findall(r'[a-zA-Z0-9]', raw_text))
            if len(cleaned) == 5:
                return cleaned

        # 增强图带检测备用
        result_det, _ = ocr(prep_bytes, use_det=True)
        if result_det:
            raw_text = "".join(x[1] for x in result_det)
            cleaned = "".join(re.findall(r'[a-zA-Z0-9]', raw_text))
            if len(cleaned) == 5:
                return cleaned
    except Exception:
        pass

    # 2. 原图直接送入识别网络
    try:
        if isinstance(image_input, bytes):
            raw_bytes = image_input
        else:
            raw_bytes = Path(image_input).read_bytes()

        result_raw_rec, _ = ocr(raw_bytes, use_det=False)
        if result_raw_rec:
            raw_text = "".join(x[0] for x in result_raw_rec)
            cleaned = "".join(re.findall(r'[a-zA-Z0-9]', raw_text))
            if len(cleaned) == 5:
                return cleaned

        result_raw, _ = ocr(raw_bytes, use_det=True)
        if result_raw:
            raw_text = "".join(x[1] for x in result_raw)
            cleaned = "".join(re.findall(r'[a-zA-Z0-9]', raw_text))
            if len(cleaned) == 5:
                return cleaned
    except Exception:
        pass

    return None
