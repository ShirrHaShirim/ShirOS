"""Shared, extensible performance identities for validation, taxonomy and UI."""

from typing import Any

ENSEMBLE_PARTS = [
    "交响乐团",
    "室内乐团",
    "弦乐团",
    "管乐团",
    "铜管乐团",
    "弦乐四重奏",
    "弦乐三重奏",
    "钢琴三重奏",
    "钢琴四重奏",
    "管乐五重奏",
    "铜管五重奏",
    "室内乐组合",
    "合唱团",
    "室内合唱团",
    "儿童合唱团",
    "摇滚乐队",
    "流行乐队",
    "爵士乐队",
    "大乐队",
    "民乐团",
    "传统音乐组合",
    "电子音乐组合",
    "声乐重唱组合",
    "打击乐团",
]

GROUPS = {
    "键盘乐器": [
        "钢琴",
        "古钢琴",
        "羽管键琴",
        "击弦古钢琴",
        "管风琴",
        "电钢琴",
        "手风琴",
        "班多钮",
        "键盘",
    ],
    "弓弦乐器": ["小提琴", "中提琴", "大提琴", "低音提琴", "维奥尔琴", "二胡", "板胡", "马头琴"],
    "拨弦乐器": [
        "主音吉他",
        "节奏吉他",
        "吉他",
        "古典吉他",
        "电吉他",
        "原声吉他",
        "贝斯",
        "电贝斯",
        "竖琴",
        "鲁特琴",
        "曼陀林",
        "班卓琴",
        "尤克里里",
    ],
    "木管乐器": [
        "长笛",
        "短笛",
        "中音长笛",
        "双簧管",
        "英国管",
        "单簧管",
        "低音单簧管",
        "巴松",
        "低音巴松",
        "萨克斯管",
        "竖笛",
        "口琴",
    ],
    "铜管乐器": ["小号", "短号", "柔音号", "圆号", "长号", "低音长号", "大号", "上低音号"],
    "打击乐器": [
        "鼓",
        "架子鼓",
        "定音鼓",
        "小军鼓",
        "大鼓",
        "马林巴",
        "颤音琴",
        "木琴",
        "钟琴",
        "康加鼓",
        "邦戈鼓",
        "手鼓",
        "塔布拉鼓",
        "打击乐",
    ],
    "传统乐器": [
        "古琴",
        "古筝",
        "琵琶",
        "阮",
        "柳琴",
        "扬琴",
        "笛子",
        "箫",
        "笙",
        "唢呐",
        "三味线",
        "尺八",
        "西塔琴",
        "萨罗德",
        "乌德琴",
        "卡农琴",
        "风笛",
    ],
    "电子演奏": ["合成器", "采样器", "特雷门琴", "电子打击乐", "唱盘"],
    "声部分工": [
        "女高音",
        "女中音",
        "女低音",
        "假声男高音",
        "男高音",
        "男中音",
        "男低音",
        "主唱",
        "和声",
        "说唱",
        "口技",
    ],
    "团体编制": ENSEMBLE_PARTS,
}

PARTS = [part for children in GROUPS.values() for part in children]
PART_ROLES = {
    part: "ensemble"
    if part in ENSEMBLE_PARTS
    else "singer"
    if part in GROUPS["声部分工"]
    else "performer"
    for part in PARTS
}
ALL_CLASSES = {"classical", "rock", "pop", "jazz", "electronic", "hiphop", "world", "other"}
# Instrumentation is independent of Genre: crossover performances remain valid.
PART_CLASSES = {part: set(ALL_CLASSES) for part in PARTS}
# Preserve established constraints on the original eight credits.
PART_CLASSES.update(
    {
        "主音吉他": {"rock", "pop"},
        "节奏吉他": {"rock", "pop", "jazz"},
        "贝斯": {"rock", "pop", "jazz"},
        "鼓": {"rock", "pop", "jazz", "world"},
        "小提琴": {"classical", "world", "jazz"},
        "中提琴": {"classical", "world"},
        "大提琴": {"classical", "world", "jazz"},
    }
)


def ui_catalogue() -> dict[str, Any]:
    return {
        "groups": GROUPS,
        "parts": {
            name: {"role": PART_ROLES[name], "classes": sorted(classes)}
            for name, classes in PART_CLASSES.items()
        },
    }
