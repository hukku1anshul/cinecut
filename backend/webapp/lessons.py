"""
Maths shortcut lessons as YouTube Shorts, in Hindi and English, made to feel like a teacher at a chalkboard.

A lesson teaches one method: the method in plain words, three worked examples (each written on a clean board, step by
step, as the teacher says it, with a piece of chalk moving along the line), then a practice problem ("your turn", a
3-2-1 countdown, then the answer). About 1.5 to 2.5 minutes, inside YouTube's 3-minute limit for Shorts.

Every example is made and checked by code, never by an AI model: each trick is a small function that picks the numbers,
works out every step and asserts that the result equals the ordinary answer; the examples in a lesson are all
different. The narration is fixed wording around those checked numbers: plain English, and pure, standard Hindi (no
English or Urdu words where a Hindi word exists), voiced in one take per lesson (yt_voice.py) so it sounds like one
person talking, and the board follows the voice word by word.

The Vedic-maths methods come from Bharati Krishna Tirtha's book "Vedic Mathematics" (published in 1965, after his death
in 1960). Methods are ideas: they are taught here in our own words and with our own examples, and every description says
where they come from (scholars have not found these sutras in the Vedas themselves).
"""
import random
import re
import subprocess
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.config import FFMPEG_BIN
from backend.video_engine import endcard
from backend.webapp import yt_voice

W, H = 1080, 1920
MAX_SECONDS = 175                # YouTube Shorts may run up to 3 minutes
LANGS = ("Hindi", "English")
TEXT = {
    "Hindi": {"closing": "बस, इतनी-सी बात है। अगली बार ऐसा कोई प्रश्न दिखे, तो यही विधि अपनाइए। ऐसी और विधियों के लिए हमारे चैनल से जुड़े रहिए।",
              "vedic": "वैदिक गणित", "shortcut": "गणित की त्वरित विधि", "checked": "CineCut · हर उदाहरण जाँचा हुआ",
              "credit": "CineCut · गणित की त्वरित विधियाँ",
              "note": ("यह विधि भारती कृष्ण तीर्थ की पुस्तक 'वैदिक गणित' (1965) से ली गई है। इसके सूत्र वेदों में नहीं मिलते; "
                       "यह बीसवीं शताब्दी में लिखी गई विधि है, जिसे यहाँ हमने अपने शब्दों और अपने उदाहरणों से समझाया है।"),
              "made": "हर उदाहरण कोड से जाँचा गया है। वाचन AI द्वारा (CineCut)।", "sutra": "सूत्र",
              "yes": "हाँ", "no": "नहीं", "example": "उदाहरण {i} / {n}", "turn": "अब आपकी बारी",
              "openers": ["चलिए, पहले {p} लेते हैं।", "अब {p}।", "और अंतिम उदाहरण: {p}।"],
              "turn_say": "अब आपकी बारी। {p}। यहाँ रुकिए और स्वयं हल करके देखिए।", "rule_lead": "विधि यह है।",
              "examples_word": "उदाहरण", "practice_word": "अभ्यास", "answer_word": "उत्तर"},
    "English": {"closing": "And that's the whole trick. Next time you see a sum like this, try it. Follow for more shortcuts like this one.",
                "vedic": "Vedic Maths", "shortcut": "Maths shortcut", "checked": "CineCut · every example checked", "credit": "CineCut · Maths shortcuts",
                "note": ("This method comes from Bharati Krishna Tirtha's book 'Vedic Mathematics' (1965). Its sutras are not found in the "
                         "Vedas; it is a 20th-century method, explained here in our own words and with our own examples."),
                "made": "Every example is checked by code. Narrated with an AI voice (CineCut).", "sutra": "Sutra",
                "yes": "Yes", "no": "No", "example": "Example {i} of {n}", "turn": "Your turn",
                "openers": ["Right, let's start with {p}.", "Now {p}.", "Last one: {p}."],
                "turn_say": "Now it's your turn. {p}. Pause the video and have a go.", "rule_lead": "Here's the trick.",
                "examples_word": "examples", "practice_word": "practice", "answer_word": "Answer"},
}
PRESET = {"Hindi": "hi", "English": "en-gb"}


def _two(n: int) -> Tuple[str, str, str]:
    """A number written with two digits: (shown, spoken in Hindi, spoken in English), e.g. 8 -> ('08', 'शून्य आठ', 'zero 8')."""
    return (f"{n:02d}", f"शून्य {n}" if n < 10 else str(n), f"zero {n}" if n < 10 else str(n))


def _say(hi: str, en: str) -> Dict[str, str]:
    return {"Hindi": hi, "English": en}


# ------------------------------------------------------------------ the tricks (each returns a checked example)
def nikhilam_below(rng: random.Random) -> Dict[str, Any]:
    a, b = rng.randint(91, 99), rng.randint(91, 99)
    x, y = 100 - a, 100 - b
    left, prod = a - y, x * y
    shown, hi, en = _two(prod)
    answer = left * 100 + prod
    assert answer == a * b
    return {"problem": f"{a} × {b}", "answer": answer, "spoken": _say(f"{a} गुणा {b}", f"{a} times {b}"),
            "hook": _say(f"{a} गुणा {b}, केवल पाँच क्षण में? संभव है। देखिए।", f"{a} times {b} in five seconds? You can. Watch."),
            "steps": [(f"{a} = 100 − {x}    {b} = 100 − {y}", _say(f"{a}, सौ से {x} कम है, और {b}, सौ से {y} कम है।",
                                                                  f"{a} is {x} short of a hundred, and {b} is {y} short.")),
                      (f"{a} − {y} = {left}", _say(f"अब तिरछा घटाइए: {a} में से {y} घटाने पर {left}। यह उत्तर का पहला भाग है।",
                                                  f"Now take {y} from {a}. That's {left}, the front of our answer.")),
                      (f"{x} × {y} = {shown}", _say(f"अब दोनों अंतरों का गुणा कीजिए: {x} गुणा {y}, अर्थात {hi}। ये अंतिम दो अंक हैं।",
                                                   f"Multiply the two gaps: {x} times {y} is {en}. Those are the last two digits.")),
                      (f"{a} × {b} = {answer}", _say(f"दोनों भाग साथ लिखिए: {answer}।", f"Put them together: {answer}."))]}


def nikhilam_above(rng: random.Random) -> Dict[str, Any]:
    a, b = rng.randint(101, 109), rng.randint(101, 109)
    x, y = a - 100, b - 100
    left, prod = a + y, x * y
    shown, hi, en = _two(prod)
    answer = left * 100 + prod
    assert answer == a * b
    return {"problem": f"{a} × {b}", "answer": answer, "spoken": _say(f"{a} गुणा {b}", f"{a} times {b}"),
            "hook": _say(f"{a} गुणा {b}? बिना लिखे, मन में ही!", f"{a} times {b}? In your head, no pen."),
            "steps": [(f"{a} = 100 + {x}    {b} = 100 + {y}", _say(f"{a}, सौ से {x} अधिक है, और {b}, सौ से {y} अधिक है।",
                                                                  f"{a} is {x} over a hundred, and {b} is {y} over.")),
                      (f"{a} + {y} = {left}", _say(f"अब तिरछा जोड़िए: {a} में {y} जोड़ने पर {left}। यह पहला भाग है।",
                                                  f"Add across: {a} plus {y} is {left}. That's the front.")),
                      (f"{x} × {y} = {shown}", _say(f"अब सौ से ऊपर के दोनों अंकों का गुणा: {x} गुणा {y}, अर्थात {hi}।",
                                                   f"Multiply the two extras: {x} times {y} is {en}.")),
                      (f"{a} × {b} = {answer}", _say(f"दोनों भाग साथ लिखिए: {answer}।", f"Put them together: {answer}."))]}


def square_ending_5(rng: random.Random) -> Dict[str, Any]:
    n = rng.randint(2, 19)
    num = 10 * n + 5
    first = n * (n + 1)
    answer = first * 100 + 25
    assert answer == num * num
    return {"problem": f"{num}²", "answer": answer, "spoken": _say(f"{num} का वर्ग", f"{num} squared"),
            "hook": _say(f"{num} का वर्ग, केवल तीन क्षण में। देखिए कैसे।", f"{num} squared in three seconds. Here's how."),
            "steps": [(f"{num} → {n} | 5", _say(f"{num} को दो भागों में देखिए: आगे {n}, और अंत में 5।",
                                               f"Split {num}: {n} at the front, 5 at the end.")),
                      (f"{n} × {n + 1} = {first}", _say(f"{n} को उससे अगली संख्या, {n + 1}, से गुणा कीजिए: {first}।",
                                                       f"Multiply {n} by the next number up, {n + 1}. That's {first}.")),
                      (f"{first} | 25", _say("अंत में बस 25 लिख दीजिए।", "Then just stick 25 on the end.")),
                      (f"{num}² = {answer}", _say(f"उत्तर: {answer}।", f"And there it is: {answer}."))]}


def times_11(rng: random.Random) -> Dict[str, Any]:
    num = rng.randint(12, 98)
    a, b = divmod(num, 10)
    s = a + b
    answer = num * 11
    assert answer == int(f"{a + s // 10}{s % 10}{b}")
    steps = [(f"{a} _ {b}", _say(f"{num} के दोनों अंकों को अलग कीजिए: {a} और {b}, बीच में स्थान छोड़िए।",
                                f"Pull {num} apart: {a} and {b}, with a gap in the middle.")),
             (f"{a} + {b} = {s}", _say(f"बीच में दोनों का योग आएगा: {a} और {b} का योग {s}।", f"The middle is their sum: {a} plus {b} is {s}."))]
    if s < 10:
        steps.append((f"{a} {s} {b}", _say(f"बीच में {s} रख दीजिए।", f"Drop {s} into the gap.")))
    else:
        steps.append((f"{a}+1 = {a + 1},  {s % 10},  {b}", _say(f"{s} दो अंकों की संख्या है, इसलिए {s % 10} बीच में लिखिए, और 1 को आगे के {a} में जोड़ दीजिए।",
                                                                 f"{s} has two digits, so {s % 10} goes in the middle and the 1 joins the {a} in front.")))
    steps.append((f"{num} × 11 = {answer}", _say(f"उत्तर: {answer}।", f"So it's {answer}.")))
    return {"problem": f"{num} × 11", "answer": answer, "spoken": _say(f"{num} गुणा 11", f"{num} times 11"),
            "hook": _say("किसी भी दो अंकों की संख्या को 11 से गुणा करने की सबसे सरल विधि।",
                         "The easiest way to multiply any two-digit number by 11."), "steps": steps}


def urdhva_2x2(rng: random.Random) -> Dict[str, Any]:
    p, q = rng.randint(12, 49), rng.randint(12, 49)
    a, b = divmod(p, 10)
    c, d = divmod(q, 10)
    right, middle, left = b * d, a * d + b * c, a * c
    r_digit, carry1 = right % 10, right // 10
    mid_total = middle + carry1
    m_digit, carry2 = mid_total % 10, mid_total // 10
    front = left + carry2
    answer = p * q
    assert int(f"{front}{m_digit}{r_digit}") == answer
    steps = [(f"{b} × {d} = {right}", _say(f"पहले इकाई के अंकों का गुणा: {b} गुणा {d}, अर्थात {right}। अंतिम अंक {r_digit}"
                                           + (f", और {carry1} हासिल।" if carry1 else "।"),
                                           f"Units first: {b} times {d} is {right}. Write {r_digit}"
                                           + (f" and carry {carry1}." if carry1 else "."))),
             (f"{a}×{d} + {b}×{c}" + (f" + {carry1}" if carry1 else "") + f" = {mid_total}",
              _say(f"अब तिरछा गुणा करके जोड़िए: {a} गुणा {d}, और {b} गुणा {c}, कुल {middle}।" + (f" हासिल का {carry1} जोड़कर {mid_total}।" if carry1 else ""),
                   f"Now the criss-cross: {a} times {d}, plus {b} times {c}, is {middle}." + (f" Add the {carry1} we carried: {mid_total}." if carry1 else ""))),
             (f"{a} × {c}" + (f" + {carry2}" if carry2 else "") + f" = {front}",
              _say(f"अब दहाई के अंकों का गुणा: {a} गुणा {c}, अर्थात {left}।" + (f" {carry2} जोड़कर {front}।" if carry2 else ""),
                   f"Then the tens: {a} times {c} is {left}." + (f" Plus {carry2} makes {front}." if carry2 else ""))),
             (f"{p} × {q} = {answer}", _say(f"सब साथ लिखिए: {answer}।", f"Read it off: {answer}."))]
    return {"problem": f"{p} × {q}", "answer": answer, "spoken": _say(f"{p} गुणा {q}", f"{p} times {q}"),
            "hook": _say(f"{p} गुणा {q}, एक ही पंक्ति में! ऊर्ध्व-तिर्यक विधि देखिए।", f"{p} times {q} in one line. Watch the criss-cross."),
            "steps": steps}


def square_near_100(rng: random.Random) -> Dict[str, Any]:
    n = rng.randint(91, 99)
    d = 100 - n
    left = n - d
    shown, hi, en = _two(d * d)
    answer = left * 100 + d * d
    assert answer == n * n
    return {"problem": f"{n}²", "answer": answer, "spoken": _say(f"{n} का वर्ग", f"{n} squared"),
            "hook": _say(f"{n} का वर्ग निकालना है? यह विधि देखिए।", f"Need {n} squared? Try this."),
            "steps": [(f"{n} = 100 − {d}", _say(f"{n}, सौ से {d} कम है।", f"{n} is {d} short of a hundred.")),
                      (f"{n} − {d} = {left}", _say(f"{n} में से उतना ही, अर्थात {d}, और घटाइए: {left}। यह पहला भाग है।",
                                                  f"Take that {d} off {n} again: {left}. That's the front.")),
                      (f"{d}² = {shown}", _say(f"अब इस अंतर का वर्ग कीजिए: {d} का वर्ग, अर्थात {hi}।", f"Square the gap: {d} squared is {en}.")),
                      (f"{n}² = {answer}", _say(f"उत्तर: {answer}।", f"So {n} squared is {answer}."))]}


def divisible_by_9(rng: random.Random) -> Dict[str, Any]:
    n = rng.randint(100000, 999999)
    if rng.random() < 0.5:
        n -= n % 9                                   # half the examples are divisible
    digits = [int(c) for c in str(n)]
    s = sum(digits)
    chain = [s]
    while chain[-1] >= 10:
        chain.append(sum(int(c) for c in str(chain[-1])))
    yes = chain[-1] == 9
    assert yes == (n % 9 == 0)
    steps = [(" + ".join(str(x) for x in digits) + f" = {s}", _say(f"संख्या के सभी अंकों को जोड़िए: योग {s}।", f"Add up the digits: {s}."))]
    if len(chain) > 1:
        steps.append((" + ".join(str(c) for c in str(s)) + f" = {chain[1]}", _say(f"{s} के अंकों को फिर जोड़िए: {chain[1]}।",
                                                                                  f"Add the digits of {s} again: {chain[1]}.")))
    steps.append((f"{chain[-1]} {'= 9 ✓' if yes else '≠ 9 ✗'}",
                  _say("9 आया, अर्थात यह संख्या 9 से पूर्णतः विभाजित होती है।" if yes
                       else f"{chain[-1]} आया, 9 नहीं, इसलिए यह संख्या 9 से पूर्णतः विभाजित नहीं होती।",
                       "We got 9, so yes, it divides by 9." if yes else f"We got {chain[-1]}, not 9, so no, it doesn't.")))
    return {"problem": f"{n} ÷ 9 ?", "answer": "yes" if yes else "no",
            "spoken": _say(f"क्या {n}, 9 से पूर्णतः विभाजित होती है", f"does {n} divide by 9"),
            "hook": _say(f"क्या {n}, 9 से पूर्णतः विभाजित होती है? भाग दिए बिना बताइए।", f"Does {n} divide by 9? No dividing allowed."), "steps": steps}


def percent_swap(rng: random.Random) -> Dict[str, Any]:
    pairs = [(x, y) for x in (4, 8, 12, 16, 18, 24, 36, 44, 48, 64) for y in (25, 50, 20) if x * y % 100 == 0]
    x, y = rng.choice(pairs)                         # only pairs whose answer is a whole number
    answer = x * y // 100
    assert x * y % 100 == 0 and answer == x * y / 100
    return {"problem": f"{x}% of {y}", "answer": answer, "spoken": _say(f"{y} का {x} प्रतिशत", f"{x} percent of {y}"),
            "hook": _say(f"{y} का {x} प्रतिशत? इसे पलट दीजिए, प्रश्न सरल हो जाएगा।", f"{x} percent of {y}? Flip it round and it's easy."),
            "steps": [(f"{x}% of {y} = {y}% of {x}", _say(f"{y} का {x} प्रतिशत, और {x} का {y} प्रतिशत, सदा बराबर होते हैं।",
                                                          f"{x} percent of {y} is exactly the same as {y} percent of {x}.")),
                      (f"{y}% of {x} = {x} × {y}/100", _say(f"और {x} का {y} प्रतिशत निकालना सरल है।", f"And {y} percent of {x} is easy.")),
                      (f"= {answer}", _say(f"उत्तर: {answer}।", f"It's {answer}."))]}


TRICKS: Dict[str, Dict[str, Any]] = {
    "nikhilam_below": {"make": nikhilam_below, "vedic": True,
                       "title": {"Hindi": "100 के निकट की संख्याओं का गुणा", "English": "Multiply numbers near 100"},
                       "sutra": {"Hindi": "निखिलं नवतश्चरमं दशतः", "English": "Nikhilam Navatashcaramam Dashatah"},
                       "rule": _say("देखिए दोनों संख्याएँ 100 से कितनी कम हैं। एक संख्या में से दूसरी का अंतर घटाइए, यह उत्तर का पहला भाग है। "
                                    "फिर दोनों अंतरों का गुणा कीजिए, ये अंतिम दो अंक हैं।",
                                    "See how far each number is below a hundred. Take one number's gap away from the other number: "
                                    "that's the front of the answer. Then multiply the two gaps: that's the last two digits.")},
    "nikhilam_above": {"make": nikhilam_above, "vedic": True,
                       "title": {"Hindi": "100 से थोड़ी बड़ी संख्याओं का गुणा", "English": "Multiply numbers just above 100"},
                       "sutra": {"Hindi": "निखिलं नवतश्चरमं दशतः", "English": "Nikhilam Navatashcaramam Dashatah"},
                       "rule": _say("देखिए प्रत्येक संख्या 100 से कितनी अधिक है। एक संख्या में दूसरी की अधिकता जोड़िए, यह पहला भाग है। "
                                    "फिर दोनों अधिकताओं का गुणा कीजिए, ये अंतिम दो अंक हैं।",
                                    "See how far each number is above a hundred. Add one number's extra to the other: that's the front. "
                                    "Then multiply the two extras: that's the last two digits.")},
    "square_ending_5": {"make": square_ending_5, "vedic": True,
                        "title": {"Hindi": "5 पर समाप्त होने वाली संख्या का वर्ग", "English": "Square numbers ending in 5"},
                        "sutra": {"Hindi": "एकाधिकेन पूर्वेण", "English": "Ekadhikena Purvena"},
                        "rule": _say("5 से पहले वाले भाग को उससे अगली संख्या से गुणा कीजिए, और अंत में 25 लिख दीजिए।",
                                     "Multiply the part before the 5 by the next number up, then write 25 on the end.")},
    "square_near_100": {"make": square_near_100, "vedic": True,
                        "title": {"Hindi": "100 के निकट की संख्या का वर्ग", "English": "Square numbers near 100"},
                        "sutra": {"Hindi": "यावदूनम्", "English": "Yavadunam"},
                        "rule": _say("संख्या 100 से जितनी कम है, उतना ही उसमें से और घटाइए, यह पहला भाग है। "
                                     "फिर उस अंतर का वर्ग कीजिए, ये अंतिम दो अंक हैं।",
                                     "Take the gap below a hundred off the number again: that's the front. "
                                     "Then square the gap: that's the last two digits.")},
    "urdhva_2x2": {"make": urdhva_2x2, "vedic": True,
                   "title": {"Hindi": "दो अंकों का गुणा एक पंक्ति में", "English": "Two-digit multiplication in one line"},
                   "sutra": {"Hindi": "ऊर्ध्व-तिर्यग्भ्याम्", "English": "Urdhva-Tiryagbhyam"},
                   "rule": _say("पहले इकाई के अंकों का गुणा, फिर तिरछा गुणा करके जोड़, फिर दहाई के अंकों का गुणा। हर बार हासिल आगे ले जाइए।",
                                "Units times units, then criss-cross and add, then tens times tens, carrying as you go.")},
    "times_11": {"make": times_11, "vedic": False, "sutra": None,
                 "title": {"Hindi": "11 से गुणा करने की सरल विधि", "English": "The multiply-by-11 trick"},
                 "rule": _say("दोनों अंकों को अलग कीजिए और बीच में उनका योग लिखिए। योग दो अंकों का हो, तो 1 आगे जोड़ दीजिए।",
                              "Split the two digits and write their sum in between. If the sum has two digits, carry the 1 to the front.")},
    "divisible_by_9": {"make": divisible_by_9, "vedic": False, "sutra": None,
                       "title": {"Hindi": "9 से विभाज्यता की पहचान", "English": "Does it divide by 9?"},
                       "rule": _say("संख्या के अंकों को जोड़िए, और तब तक जोड़ते रहिए जब तक एक ही अंक न बचे। वह 9 हो, तो संख्या 9 से पूर्णतः विभाजित होती है।",
                                    "Add up the digits, again and again, until one digit is left. If it's 9, the number divides by 9.")},
    "percent_swap": {"make": percent_swap, "vedic": False, "sutra": None,
                     "title": {"Hindi": "प्रतिशत पलटने की विधि", "English": "The percentage flip"},
                     "rule": _say("y का x प्रतिशत, और x का y प्रतिशत, सदा बराबर होते हैं। जो सरल हो, वही निकालिए।",
                                  "x percent of y is always the same as y percent of x, so work out whichever is easier.")},
}


def example(trick: str, seed: int) -> Dict[str, Any]:
    ex = TRICKS[trick]["make"](random.Random(seed))
    ex.update(trick=trick, seed=seed)
    return ex


def lesson(trick: str, seed: int, n: int = 3) -> Dict[str, Any]:
    """n different worked examples and one different practice problem for one method."""
    exs, seen = [], set()
    for s in range(seed * 1000, seed * 1000 + 500):
        e = example(trick, s)
        if e["problem"] not in seen:
            seen.add(e["problem"])
            exs.append(e)
        if len(exs) > n:
            break
    return {"trick": trick, "seed": seed, "examples": exs[:n], "practice": exs[n] if len(exs) > n else None}


# ------------------------------------------------------------------ the chalkboard
CHALK, YELLOW, BLUE = "&H00EEF2F2&", "&H0080E6F7&", "&H00F0D2A0&"


def _ass(t: str) -> str:
    return t.replace("\\", " ").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def _ts(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _highlight(show: str) -> str:
    """The result after the last '=' in yellow chalk, e.g. '96 − 7 = {yellow}89'."""
    m = re.match(r"^(.*=\s*)([\d,]+)$", show)
    if not m:
        return _ass(show)
    return f"{_ass(m.group(1))}{{\\c{YELLOW}}}{m.group(2)}"


def _blob(r: int) -> str:
    k = round(0.552 * r)
    return f"m {-r} 0 b {-r} {-k} {-k} {-r} 0 {-r} b {k} {-r} {r} {-k} {r} 0 b {r} {k} {k} {r} 0 {r} b {-k} {r} {-r} {k} {-r} 0"


def _stroke(rng: random.Random, width: int, thick: int = 8) -> str:
    """A hand-drawn chalk underline: a slightly wavy, slightly tilted band."""
    n = 10
    tilt = rng.uniform(-6, 6)
    top = [(int(width * i / n), int(tilt * i / n + rng.uniform(-2.5, 2.5))) for i in range(n + 1)]
    bot = [(x, y + thick + int(rng.uniform(-1.5, 1.5)) - (3 if i in (0, n) else 0)) for i, (x, y) in enumerate(top)][::-1]
    pts = top + bot
    return "m " + " ".join(f"{x} {y}" if i != 1 else f"l {x} {y}" for i, (x, y) in enumerate(pts))


TICK = "m 0 26 l 10 18 22 34 52 0 60 8 22 50"
CHALK_STICK = "m 0 0 l 44 0 46 3 46 11 44 14 0 14 -2 11 -2 3"


def _latin(t: str) -> bool:
    return not re.search(r"[ऀ-ॿ]", t)


HEAD = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Kicker,Nirmala UI,42,{YELLOW},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,8,60,60,150,1\n"
        f"Style: Title,<title_font>,64,{CHALK},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,8,70,70,220,1\n"
        f"Style: Chip,<chip_font>,48,{BLUE},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,8,60,60,0,1\n"
        f"Style: Problem,Ink Free,150,{YELLOW},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,8,60,60,0,1\n"
        f"Style: Rule,<rule_font>,58,{CHALK},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,8,90,90,0,1\n"
        f"Style: Step,Ink Free,80,{CHALK},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,8,50,50,0,1\n"
        f"Style: Answer,Ink Free,112,{CHALK},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,8,50,50,0,1\n"
        f"Style: Count,Ink Free,300,{YELLOW},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n"
        f"Style: Foot,Nirmala UI,32,&H00A0B0A8,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,60,60,110,1\n"
        f"Style: Shape,Arial,20,{CHALK},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
SIZE = {"Problem": 150, "Step": 80, "Answer": 112}


def _ev(layer: int, a: float, b: float, style: str, text: str) -> str:
    return f"Dialogue: {layer},{_ts(a)},{_ts(b)},{style},,0,0,0,,{text}"


def _plain(text: str) -> str:
    return re.sub(r"\{[^}]*\}", "", text)


def _write(layer: int, a: float, b: float, style: str, y: int, text: str, height: int, ms: int, rng: Optional[random.Random] = None) -> List[str]:
    """Text written by hand, left to right, at the pace of writing: a piece of chalk travels along the line as it appears,
    and a little chalk dust falls when the line is finished."""
    width = min(1000, int(len(_plain(text)) * SIZE.get(style, 80) * 0.5) + 40)
    x0, x1 = 540 - width // 2, 540 + width // 2
    out = [_ev(layer, a, b, style, f"{{\\an8\\pos(540,{y})\\blur0.7\\clip({x0},{y - 10},{x0 + 1},{y + height})"
                                   f"\\t(0,{ms},\\clip({x0},{y - 10},{x1},{y + height}))\\fad(0,250)}}{text}")]
    if rng is None or ms < 50:
        return out
    yb = y + int(height * 0.62)
    out.append(_ev(layer + 2, a, a + ms / 1000 + 0.35, "Shape",
                   f"{{\\an7\\move({x0},{yb},{x1 - 20},{yb - 6},0,{ms})\\frz28\\c&H00F4F6F6&\\blur0.8\\fad(120,300)\\p1}}{CHALK_STICK}{{\\p0}}"))
    t = a + ms / 1000
    for _ in range(5):                                # dust falling from the chalk
        dx, dy = rng.randint(-30, 20), rng.randint(40, 110)
        r = rng.randint(2, 4)
        out.append(_ev(layer + 1, t, t + 0.9, "Shape",
                       f"{{\\an7\\move({x1 - 20},{yb},{x1 - 20 + dx},{yb + dy},0,850)\\1a&H60&\\blur1.5\\fad(0,600)\\p1}}{_blob(r)}{{\\p0}}"))
    return out


def _write_ms(text: str) -> int:
    return max(350, min(1300, 55 * len(_plain(text))))


def _board(ex: Dict[str, Any], chip: str, seg_start: float, seg_end: float, step_times: List[float], rng: random.Random,
           instant: bool = False) -> List[str]:
    """One example on a clean board: its label, the problem written up, then each step as it is said; the answer underlined."""
    ev = [_ev(1, seg_start, seg_end, "Chip", f"{{\\an8\\pos(540,380)\\fad(250,200)}}{_ass(chip)}")]
    if not instant:
        ev += _write(1, seg_start, seg_end, "Problem", 460, _ass(ex["problem"]), 190, _write_ms(ex["problem"]) + 200, rng)
    y = 760
    steps = ex["steps"]
    for k, (show, _) in enumerate(steps):
        start = step_times[k]
        last = k == len(steps) - 1
        ms = 1 if instant else _write_ms(show)
        if last:
            ev += _write(1, start, seg_end, "Answer", y, _highlight(show), 150, ms, None if instant else rng)
            width = min(900, int(len(show) * 52))
            x0 = 540 - width // 2
            ev.append(_ev(2, start + ms / 1000 + 0.1, seg_end, "Shape",
                          f"{{\\an7\\pos({x0},{y + 138})\\c{YELLOW}\\blur0.8\\clip({x0},{y + 120},{x0 + 1},{y + 160})"
                          f"\\t(0,450,\\clip({x0},{y + 120},{x0 + width + 10},{y + 160}))\\p1}}{_stroke(rng, width)}{{\\p0}}"))
            ev.append(_ev(2, start + ms / 1000 + 0.55, seg_end, "Shape",
                          f"{{\\an7\\pos({x0 + width + 30},{y + 30})\\c{YELLOW}\\blur0.8\\fscx40\\fscy40\\t(0,180,\\fscx100\\fscy100)\\p1}}{TICK}{{\\p0}}"))
        else:
            ev += _write(1, start, seg_end, "Step", y, _highlight(show), 110, ms, None if instant else rng)
        y += 190 if last else 140
    return ev


def _board_png(path: Path, seed: int) -> None:
    """A dark green-grey chalkboard with a little grain and darker corners (made once per lesson)."""
    subprocess.run([FFMPEG_BIN, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x26352e:s={W}x{H}:d=1", "-vf",
                    f"noise=alls=16:allf=u:all_seed={seed % 100000},gblur=sigma=1.4,eq=contrast=1.05,vignette=PI/4.2",
                    "-frames:v", "1", str(path)], check=True)


def render_lesson(ls: Dict[str, Any], out_dir: Path, work: Path, language: str = "Hindi", preset: Optional[str] = None,
                  progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Voices and renders one lesson Short in `language`: the method, worked examples, practice. Returns files and timings."""
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    preset = preset if preset in yt_voice.PRESETS and yt_voice.PRESETS[preset]["language"] == language else PRESET[language]
    info, txt = TRICKS[ls["trick"]], TEXT[language]
    first = ls["examples"][0]
    # the parts of the lesson; the whole lesson is voiced in one take, cut into parts and lines by the words themselves
    parts: List[Dict[str, Any]] = [{"kind": "intro", "lines": [first["hook"][language], f"{txt['rule_lead']} {info['rule'][language]}"]}]
    for i, e in enumerate(ls["examples"]):
        opener = txt["openers"][min(i, len(txt["openers"]) - 1)].format(p=e["spoken"][language])
        parts.append({"kind": "example", "ex": e, "lines": [opener] + [say[language] for _, say in e["steps"]]})
    p = ls.get("practice")
    if p:
        parts.append({"kind": "practice", "ex": p, "lines": [txt["turn_say"].format(p=p["spoken"][language])]})
        parts.append({"kind": "answer", "ex": p, "lines": [p["steps"][-1][1][language]]})
    parts.append({"kind": "closing", "lines": [txt["closing"]]})
    files, engine = yt_voice.voice_parts([" ".join(pt["lines"]) for pt in parts], preset, work, teacher=True, progress=progress)
    for k, (pt, f) in enumerate(zip(parts, files)):
        pt["wav"] = work / f"take{k}.wav"
        pt["dur"] = yt_voice.to_wav(f, pt["wav"])
        pt["offsets"] = yt_voice.starts(pt["wav"], pt["lines"], pt["dur"], language, work)

    def layout(items: List[Dict[str, Any]]) -> float:
        t = 0.5
        for pt in items:
            if pt["kind"] == "answer":
                t += 3.3                                  # the pause to try it: 3, 2, 1
            pt["start"] = t
            pt["times"] = [t + o for o in pt["offsets"]]
            t += pt["dur"] + (0.9 if pt["kind"] in ("example", "answer") else 0.45)
            pt["end"] = t
        return t + 0.4

    total = layout(parts)
    examples = [pt for pt in parts if pt["kind"] == "example"]
    while total > MAX_SECONDS and len(examples) > 2:     # too long for a Short: one example fewer
        parts.remove(examples[-1])
        examples = examples[:-1]
        total = layout(parts)
    n = len(examples)
    with wave.open(str(parts[0]["wav"]), "rb") as w0:
        rate, width = w0.getframerate(), w0.getsampwidth()
    audio = bytearray(int(total * rate) * width)
    for pt in parts:
        with wave.open(str(pt["wav"]), "rb") as w:
            frames = w.readframes(w.getnframes())
        pos = int(pt["start"] * rate) * width
        audio[pos:pos + len(frames)] = frames[: max(0, len(audio) - pos)]
    with wave.open(str(work / "narration.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(audio))
    # the board
    rng = random.Random(ls["seed"] * 7 + len(language))
    ev = []
    for _ in range(6):                                    # faint smudges of old chalk
        r = rng.randint(90, 220)
        ev.append(_ev(0, 0, total, "Shape", f"{{\\an7\\pos({rng.randint(0, W)},{rng.randint(0, H)})\\1a&HF0&\\blur40\\p1}}{_blob(r)}{{\\p0}}"))
    sutra = (info.get("sutra") or {}).get(language)
    kicker = (txt["vedic"] + (f" · {sutra}" if sutra else "")) if info["vedic"] else txt["shortcut"]
    title = info["title"][language]
    ev += [_ev(1, 0, total, "Kicker", f"{{\\fad(400,0)}}{_ass(kicker)}"),
           _ev(1, 0, total, "Title", f"{{\\fad(500,0)}}{_ass(title)}"),
           _ev(1, 0, total, "Foot", _ass(txt["checked"]))]
    thumb_at = None
    for idx, pt in enumerate(parts):
        if pt["kind"] == "intro":
            ev += _write(1, pt["times"][0], pt["end"], "Problem", 460, _ass(first["problem"]), 190, _write_ms(first["problem"]) + 200, rng)
            ev.append(_ev(1, pt["times"][1], pt["end"], "Rule", f"{{\\an8\\pos(540,800)\\fad(400,250)}}{_ass(info['rule'][language])}"))
        elif pt["kind"] == "example":
            chip = txt["example"].format(i=examples.index(pt) + 1, n=n)
            ev += _board(pt["ex"], chip, pt["start"], pt["end"], pt["times"][1:], rng)
            if thumb_at is None:
                thumb_at = pt["times"][-1] + 1.4
        elif pt["kind"] == "practice":
            nxt = parts[idx + 1]
            ev.append(_ev(1, pt["start"], nxt["end"], "Chip", f"{{\\an8\\pos(540,380)\\fad(250,200)}}{_ass(txt['turn'])}"))
            ev += _write(1, pt["start"], nxt["end"], "Problem", 460, _ass(pt["ex"]["problem"]), 190, _write_ms(pt["ex"]["problem"]) + 200, rng)
            for j, digit in enumerate(("3", "2", "1")):
                a = pt["end"] + 0.1 + j * 1.05
                ev.append(_ev(3, a, a + 0.95, "Count", f"{{\\an5\\pos(540,1150)\\blur0.8\\fscx70\\fscy70\\t(0,200,\\fscx100\\fscy100)\\fad(60,250)}}{digit}"))
        elif pt["kind"] == "answer":
            steps = pt["ex"]["steps"]
            ev += _board(pt["ex"], txt["turn"], pt["start"], pt["end"], [pt["start"]] * len(steps), rng, instant=True)[1:]
        else:
            ev.append(_ev(1, pt["times"][0], total, "Rule", f"{{\\an5\\pos(540,1000)\\fad(300,0)}}{_ass(txt['closing'])}"))
    head = (HEAD.replace("<title_font>", "Ink Free" if _latin(title) else "Nirmala UI")
            .replace("<chip_font>", "Ink Free" if language == "English" else "Nirmala UI")
            .replace("<rule_font>", "Ink Free" if language == "English" else "Nirmala UI"))
    (work / "short.ass").write_text(head + "\n".join(ev) + "\n", encoding="utf-8")
    _board_png(work / "board.png", ls["seed"])
    video = out_dir / "short.mp4"
    cmd = [FFMPEG_BIN, "-y", "-v", "error", "-loop", "1", "-framerate", "30", "-i", "board.png", "-i", "narration.wav",
           "-filter_complex", "[0:v]ass=short.ass,format=yuv420p[v];[1:a]loudnorm=I=-15:TP=-1.5:LRA=9[a]", "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-tune", "stillimage", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
           "-t", f"{total:.2f}", "-movflags", "+faststart", str(video.resolve())]
    r = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not video.exists():
        raise RuntimeError("Short failed: " + ((r.stderr or "").strip().splitlines() or ["?"])[-1][:200])
    thumb = out_dir / "thumb.jpg"
    subprocess.run([FFMPEG_BIN, "-y", "-v", "error", "-ss", f"{(thumb_at or 5.0):.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(thumb)],
                   capture_output=True)
    endcard.append_end_card(str(video), txt["credit"], title, language, 3.0)
    return {"video": video.name, "thumb": thumb.name if thumb.exists() else None, "seconds": round(total + 3.0, 1), "engine": engine,
            "examples": n, "practice": bool(p)}


def metadata(ls: Dict[str, Any], language: str = "Hindi", n_examples: Optional[int] = None) -> Dict[str, Any]:
    info, txt = TRICKS[ls["trick"]], TEXT[language]
    exs = ls["examples"][: n_examples or len(ls["examples"])]
    series = txt["vedic"] if info["vedic"] else txt["shortcut"]
    title = f"{info['title'][language]} | {series} #Shorts"
    lines = [info["rule"][language], ""]
    for i, e in enumerate(exs, 1):
        answer = TEXT[language][e["answer"]] if e["answer"] in ("yes", "no") else e["answer"]
        lines.append(f"{txt['example'].format(i=i, n=len(exs))}: {e['problem']} = {answer}")
        lines += [f"  • {say[language]}" for _, say in e["steps"]]
    if ls.get("practice"):
        pr = ls["practice"]
        answer = TEXT[language][pr["answer"]] if pr["answer"] in ("yes", "no") else pr["answer"]
        lines += ["", f"{txt['turn']}: {pr['problem']}  ({txt['answer_word']}: {answer})"]
    lines.append("")
    if info["vedic"]:
        lines += [f"{txt['sutra']}: {info['sutra'][language]}", txt["note"], ""]
    tag_line = "#Shorts #MathsTricks" + (" #गणित" if language == "Hindi" else " #MentalMaths")
    if info["vedic"]:
        tag_line += " #VedicMaths" + (" #वैदिकगणित" if language == "Hindi" else "")
    lines += [txt["made"], "", tag_line]
    if language == "Hindi":
        tags = ["गणित", "गणित की विधियाँ", "वैदिक गणित", "मानसिक गणित", "maths tricks", "SSC maths", "class 10 maths"]
    else:
        tags = ["maths tricks", "mental maths", "maths shortcut", "speed maths", "multiplication trick", "SSC maths", "GCSE maths"]
    if info["vedic"]:
        tags = ["vedic maths", info["sutra"][language]] + tags
    return {"title": title[:100], "description": "\n".join(lines), "tags": list(dict.fromkeys(tags)),
            "language": "hi" if language == "Hindi" else "en", "category_id": "27", "made_for_kids": False, "short": True}
