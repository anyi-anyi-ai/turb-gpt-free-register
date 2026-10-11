import os
import re
import urllib.request
import tempfile
import logging

logger = logging.getLogger("audio_solver")

# 全局加载模型，避免每次调用重复初始化
_model = None

def get_whisper_model():
    global _model
    if _model is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError("请先安装 faster-whisper: pip install faster-whisper")
            
        logger.info("💿 正在将 faster-whisper (base.en) 模型载入内存 (INT8/CPU) ...")
        # 为追求极限启动速度和普适性，默认使用 CPU 和 INT8 量化，1秒内即可完成极速推断
        _model = WhisperModel("base.en", device="cpu", compute_type="int8")
    return _model

def extract_numbers_from_text(text: str) -> str:
    """
    智能抽取转录文本中的数字。
    Arkose 音频通常会说："Please type the numbers you hear. 4 2 8 1 0" 或 "four, two, eight"
    """
    number_map = {
        'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
        'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
        'oh': '0'
    }
    
    # 规范化文本，去除标点符号
    normalized = re.sub(r'[^\w\s]', ' ', text.lower())
    
    result = []
    for word in normalized.split():
        if word in number_map:
            result.append(number_map[word])
        elif word.isdigit():
            # 进一步拆分连在一起的数字串 (比如 "42810")
            for char in word:
                if char.isdigit():
                    result.append(char)
                    
    return "".join(result)

def extract_answer_from_transcription(text: str, instruction: str = "") -> str:
    """
    自适应双模答案解析器：
    1. 模态 A: 声学辨析选择题 (如 "Which sound is the bird? Option 1... Option 2... Option 3...")
    2. 模态 B: 数字朗读听写题 (如 "Please type the numbers you hear: 4 2 8 1 0")
    """
    clean_text = text.lower()
    clean_inst = instruction.lower().strip()

    # 模态 A 检测：若转录文本包含 "option 1" / "option 2" 等选项划分
    if "option 1" in clean_text or "option 2" in clean_text:
        logger.info("🧩 检测到声学辨析选择题 (Option 1/2/3 模式)，执行语义匹配...")
        # 拆分选项
        options_split = re.split(r'option\s+(\d+)', clean_text)
        # options_split 结构通常为: [前导词, '1', '内容1', '2', '内容2', ...]
        best_option = "1"
        if len(options_split) >= 3:
            # 提取题干中的核心关键词 (例如: "bird", "dog", "train", "bell", "applause")
            keywords = [w for w in re.findall(r'\b[a-zA-Z]{3,}\b', clean_inst) if w not in ('which', 'sound', 'select', 'the', 'is', 'that', 'with', 'has')]
            for i in range(1, len(options_split), 2):
                opt_num = options_split[i]
                opt_content = options_split[i+1] if i+1 < len(options_split) else ""
                # 若题干关键词命中了该选项的描述
                if any(kw in opt_content for kw in keywords):
                    logger.info(f"🎯 选项 {opt_num} 命中题干语义关键词: {keywords}")
                    return opt_num
        return best_option

    # 模态 B：传统数字听写抽取
    return extract_numbers_from_text(text)

def solve_audio_file(file_path: str, instruction: str = "") -> str:
    """
    给定本地音频文件路径，进行本地极速识别，返回数字或选项答案。
    """
    try:
        logger.info("🧠 启动本地 Whisper 推理...")
        model = get_whisper_model()
        segments, info = model.transcribe(file_path, beam_size=1, language="en")
        
        full_text = " ".join([segment.text for segment in segments])
        logger.info(f"📝 原始转录文本: {full_text.strip()}")
        
        solution = extract_answer_from_transcription(full_text, instruction=instruction)
        logger.info(f"🎯 提取解: 【{solution}】")
        
        return solution
        
    except Exception as e:
        logger.error(f"❌ 语音打码管线崩溃: {e}")
        return ""

# 向后兼容别名
solve_audio_url = solve_audio_file
