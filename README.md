# CineCut AI Studio 10.0

CineCut condenses long videos into shorter cuts on your own PC, and writes its own narrated explainers of books, lectures and stories. Every source goes through a rights check first (see "Version 10" below). It works on two kinds of video:

- **Movies and TV:** a 2-3 hour film becomes a 5-45 minute story cut that keeps the setup, turning points, climax and ending.
- **Lectures, tutorials and webinars:** a long recording becomes a study cut that keeps the definitions, explanations, examples and summaries, plus study notes with flashcards.

It also makes 9:16 shorts, chapter markers, subtitles, DaVinci/Premiere timelines, narration in Hindi, English and other languages, and a watch guide for films on Netflix or Prime Video.

## Quick start

```
CineCut_OneClick_Setup.bat      (first time: installs Python packages, FFmpeg, Deno)
python run.py                   (starts the studio at http://localhost:8080)
python run.py --lan             (also shares the TV player with devices on your Wi-Fi)
```

Requirements: Python 3.10+, FFmpeg on PATH. An NVIDIA GPU is used automatically for encoding when present. YouTube downloads need Deno (`winget install DenoLand.Deno`).

## How a cut is made

1. **Transcript:** an external `.srt`/`.vtt` file, a subtitle file next to the video, an embedded subtitle track, YouTube captions, or local Whisper transcription. Films with no subtitles are transcribed automatically with Groq's free Whisper models when a Groq key is set; the transcript is saved and reused.
2. **Full-film analysis** (in parallel): every shot change, dialogue pauses, and per-second loudness. A 3-hour film takes about a minute.
3. **Scene selection:**
   - Movies use story beats from the essence database, from the film's own dialogue (read once by a free AI model), from the AI's knowledge of the film, or, as a last resort, a generic three-act template. Beats come in three tiers: a short cut keeps the core beats, and longer cuts add supporting and extra beats instead of stretching the same scenes. Once each beat has about two minutes, extra time goes to bridge scenes: the most important dialogue in the longest stretch still cut out. Clip edges are moved to the start or end of a spoken line.
   - **What to include:** tick any of Songs, Action, Comedy, Emotional, Romance, Hero and Villain, several at once, and choose how much of the time they get. Songs, action, comedy and emotional scenes are also detected from captions, loudness and editing pace, and every filter promotes the story beats the AI tagged with it. Type a character's name to keep more of their scenes. Each scene card shows which filter picked it.
   - Lectures are split into topics at slide changes and pauses. Topics with definitions, examples and summaries rank highest; housekeeping and tangents are dropped.
4. **Render:** every clip is cut and encoded once, in parallel, with soft transitions and optional narration with audio ducking. Loudness is normalized to EBU R128, and chapters are built from the measured clip lengths.

The target length is honoured within about 8% unless the film itself is too short.

## Adding films to the essence database

Edit `backend/video_engine/essence_engine.py` and add an entry to `OFFLINE_ESSENCE_DATABASE`. The comment at the top of the database explains the fields. Prefer `target_percent` positions, which work for any copy of the film. If you use hand-timed `start_sec`/`end_sec`, also set `reference_duration_sec` to the exact length of the file you timed; copies of a different length are then scaled and the UI warns that timing is approximate. Film names are matched as whole words, so "India.mp4" no longer matches "Mr. India".

## Keys and settings (.env)

Put keys in the file `.env` in the CineCut folder, then restart CineCut. Several keys per provider are allowed (`_2`, `_3`, ...):

```
GEMINI_API_KEY=...        GEMINI_API_KEY_2=...
GROQ_API_KEY=...
CLOUDFLARE_API_KEY=...    CLOUDFLARE_ACCOUNT_ID=...
NVIDIA_API_KEY=...
CINECUT_AZURE_SPEECH_KEY=...   CINECUT_AZURE_SPEECH_REGION=...
```

**Free tiers only.** CineCut uses free-tier models (Gemini Flash, Groq, Cloudflare Workers AI, NVIDIA trial credits) in that order, rotates between keys, rests a key when the provider says it is rate limited, and stops using a key at a daily request cap (`temp/llm_usage.json`; change with `CINECUT_GEMINI_DAILY_CAP`, `CINECUT_GROQ_DAILY_CAP`, `CINECUT_CLOUDFLARE_DAILY_CAP`, `CINECUT_NVIDIA_DAILY_CAP`). If every key is used up, a local Ollama model takes over. A cap limits usage but cannot see billing: a key is only guaranteed free if its account has no paid plan or billing attached. Keep `.env` private.

**Transcription without subtitles.** With a Groq key, the Transcribe button sends the audio track (not the picture) to Groq's free Whisper models in 10-minute parts. Analysis does this automatically for films without subtitles. When one model's hourly audio limit is reached the other model is used, and CineCut waits only if both are limited.

## Gemini (optional)

With a Google AI Studio key, Gemini picks scenes from the transcript (the AI Director; if it picks less than you asked for, the most important story scenes are added to reach your length), looks up story beats for films not in the database, analyzes lectures, and writes narration. CineCut uses the newest Gemini Flash model available to your key (fallback `gemini-3.8-flash`; override with the `CINECUT_GEMINI_MODEL` environment variable). The key is sent in a header, never in the URL. On the free tier Google may use prompts to improve its products.

## Narration that only covers what was cut

By default each narration line is spoken as a kept scene starts and briefly names the main events of the part cut out just before it (at most 30 words), written from that part's dialogue, never from earlier or later events. Films in the essence database also offer their hand-written story lines as a narration choice. Lines are checked for names that are not in that dialogue and for phrases like "later in the film"; a line that fails falls back to a plain time skip. Each scene card shows which part of the film its narration covers.

## Narration voices

The free voices use Microsoft Edge's online read-aloud service through `edge-tts`. They need internet and are meant for personal use. For monetized videos, record your own voice or set `CINECUT_AZURE_SPEECH_KEY` and `CINECUT_AZURE_SPEECH_REGION` (or enter them in the narrator panel) to use Azure AI Speech, which includes the same Hindi voices.

## YouTube embed cut (share-safe)

For videos downloaded from YouTube, **YouTube Embed Cut** makes a page that plays the original upload through YouTube's official embedded player, jumping between your chosen scenes and showing (or reading aloud) the narration in between. Nothing is copied or re-uploaded, so views and ads stay with the owner. If the owner disables embedding, the page lists timestamped links instead.

## Netflix and Prime Video: the watch guide

CineCut does not and cannot copy video from Netflix or Prime Video. Their streams are DRM-protected, their terms forbid copying, and bypassing DRM is illegal (US 17 U.S.C. 1201, India s.65A). Instead:

- **Watch guide** (Netflix / Prime Guide button): enter a film title and runtime, and CineCut lists which timestamps to jump to so you watch the condensed story inside the official app, with a short recap of what you skip. An MP3 audio recap is available too.
- **Browser extension** (`browser_extension/`): shows the guide as a panel on the Netflix or Prime Video page and can jump the official player between key scenes. Install it from `chrome://extensions` with Developer mode, then Load unpacked. It never reads or records the stream. Seeking depends on those sites' web players and may break when they change.

## YouTube and copyright

Read this before uploading anything made from copyrighted films:

- Condensing a whole film into a narrated recap uses a large part of the work and can replace watching it. Courts have treated narrated recaps of films as infringing (Tokyo District Court 2022, ¥500 million; the 2026 KADOKAWA subpoena ruling in California).
- YouTube's Content ID can block a video or send its revenue to the rights holder. Three copyright strikes within 90 days terminate a channel. Mirroring, cropping or short clips do not reliably avoid matching and do not change the legal position.
- YouTube's monetization rules treat narrated compilations without real commentary as reused content, and templated AI-voiced videos as inauthentic content.
- The Copyright Risk Check panel shows how much of the film you used, clip lengths and commentary coverage. It is a checklist, not legal protection. The counter-notice file is a draft that you must only complete with true statements.
- Lower-risk uses: your own recordings and lectures, openly licensed material (e.g. NPTEL CC BY-SA, Blender open movies; MIT OpenCourseWare is non-commercial only), public-domain films, and commentary-led reviews that use short clips.

This is general information, not legal advice.

## Other features

- **Shorts / Reels:** picks the strongest moments by story importance, loudness and editing pace, starts and ends them on line breaks, then frames them in 9:16 with face-tracking pan & scan (OpenCV) or blurred side bars, with optional burned-in captions (caption tags like [Music] are left out). When few faces are found, and always for lectures, the whole frame is kept so dancers, wide shots and blackboards stay visible.
- **Trailer:** Make Trailer builds a 60-second, 90-second or 2-minute trailer from the film: complete setup lines from the core story beats, short build-up moments, a fast montage of the loudest, fastest-cut shots, a closing line and a title card. It uses only the first 65% of the film, so there are no spoilers, and can also be made in 9:16 for Reels.
- **Lectures:** a longer study cut covers more topics instead of longer blocks, and every lecture cut (including the AI Director's) is fitted to your length and ends on a line break. Hindi narration may keep English technical terms such as Big O.
- **YouTube Upload Pack:** title, description with valid YouTube chapters, tags, credit line and a pre-upload checklist. Upload manually in YouTube Studio; API uploads from unaudited projects are forced to private.
- **TV season batch:** one recap for a whole season with a chapter per episode.
- **Watchfolder:** writes a `[Movie] - Recap.mp4` next to each new film in a Plex/Jellyfin folder.
- **Storage:** shows disk usage and cleans old temporary files. Rendered videos are never deleted.

## Version 11: the web app (http://localhost:8080/app/)

The studio at `/` stays on this PC. The web app at `/app/` is what customers use; with `--lan` it is reachable from other devices, and for a public domain name add it to `CINECUT_PUBLIC_HOSTS`.

- **Accounts, plans and payments.** Sign-up with email and password (the Terms must be accepted), sessions in `data/cinecut.db`. Plans cover the five ways CineCut earns: Library Plus, Creator, Creator Pro, Institute, Company training (per seat) and Partner API. **Everything is free for now** (`CINECUT_PAYMENTS_FREE=1`): choosing a plan starts it for 30 days at ₹0. To charge later, set `CINECUT_PAYMENTS_FREE=0` and your Razorpay keys `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` (webhook URL: `/api/app/razorpay/webhook`).
- **Library of recipes, not videos.** Each title is a few kilobytes in `data/library/`: where the original lives (Internet Archive or Wikimedia Commons file, YouTube embed, or a workspace's own upload), the clip times, the narration text per language, or for books the explainer script. The player streams the clips from the original site and voices the narration when it is first needed; voiced pieces sit in a 300 MB cache (`CINECUT_LIBRARY_CACHE_MB`) that drops the least recently played. Add titles from the studio with **Add to library** (films, series episodes, lectures, explainers), or let users ask for a public-domain book by its Gutenberg number.
- **Watch or listen.** Every title plays as video (or slides for books) or in listen mode with background play, lock-screen controls and 0.75-2× speed. YouTube titles are video-only, as YouTube's terms require its player to be shown.
- **Credits end card on every video**: rendered summaries, trailers, reels, dubs and explainers end with the source credit and "made with AI"; the web player ends with the same card.
- **Creator plan**: upload your own video (with a rights declaration) and get a condensed cut, three 9:16 reels with karaoke captions, a dub in Hindi or a regional language (translated to fit the timing, the original soundtrack ducked underneath, subtitles included) or an own-words explainer.
- **Institutes and companies**: workspaces with invite codes, admins and members. Admins upload lectures, training videos or manuals; CineCut makes study cuts and own-words explainers with quizzes, visible only to members, and a completion report.
- **Partner API** (`/api/v1/...`, key in `X-API-Key`): catalogue with credits and licences, play recipes with narration text, narration audio, and explainer requests. Every call is logged per title for revenue share; 120 calls a minute per key.
- **Languages**: narration and explainers in English, Hindi, Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada, Malayalam and Punjabi (Sarvam voices when `SARVAM_API_KEY` is set, Edge otherwise).
- **AI keys**: every key is tracked per model against its per-minute, per-hour and per-day free limits; a 429 rests only that key and model for as long as the provider says, and a key is used again as soon as its window rolls over.

### Version 11.1: a full shelf, voice removal and every key used

- **Library of 300+ titles per shelf.** `POST /api/studio/library/harvest` (or `backend/webapp/catalog.py`) fills every shelf (movies, series, books, lectures, stories) with public-domain or openly licensed titles: Internet Archive films and classic TV episodes published by 1965, Project Gutenberg books and stories whose authors and translators died by 1965, and Creative Commons lectures on YouTube. Each is a catalogue entry of a couple of kilobytes; films, episodes and lectures can be watched in full from their source at once.
- **Background builder** (`backend/webapp/builder.py`, on by default; `CINECUT_LIBRARY_BUILDER=0` turns it off) writes the summaries within the free tiers: titles people ask for first ("Make the summary"), otherwise each shelf in turn. Books, stories and lectures take a few minutes each; films and episodes need transcription, so their pace is set by Groq's free audio allowance (about 8 hours of audio per key per model per day). Summaries are written in `CINECUT_LIBRARY_LANGUAGE` (Hindi by default); narration audio is made only when someone plays a title. Lines that would only say "the story moves ahead N minutes" (where a film has no dialogue to go on) are left out; those clips play without a voice. Sexploitation titles are kept off the shelves.
- **Dubs remove the original voice.** Demucs (Meta's open htdemucs model, `pip install demucs`, weights downloaded once) splits the soundtrack into voice and music-and-effects on the GPU; the new voice is laid over music and effects only. Without Demucs, the original is lowered under each line instead.
- **Every key, every model, every window.** All `GEMINI_API_KEY*` and `GROQ_API_KEY*` keys are rotated. Each key is tracked per model against its per-minute, per-hour and per-day limits (and Groq Whisper's audio seconds per hour and per day); a limit rests only that key and model until its window rolls over. Add more keys as `GROQ_API_KEY_2`, `GEMINI_API_KEY_4` and so on to raise the daily totals.

### Version 11.2: every title, Hindi classics and country rules

- **No cap on the shelves.** `catalog.harvest()` (or `POST /api/studio/library/harvest` with no `per_type`) takes every title the sources offer: Internet Archive films and classic TV published by 1965 and marked public domain or with a Creative Commons licence that allows commercial use (NonCommercial and NoDerivatives titles are left out), every English and Hindi Project Gutenberg book and story whose authors and translators all died by 1965, and Creative Commons lectures from about 230 topic searches on YouTube (including NPTEL's IIT and IISc courses). Another upload of a film already on the shelf, or another edition of a book, is listed once. The library keeps an in-memory index updated title by title, so tens of thousands of recipes browse in milliseconds.
- **Hindi classics from Hindi Wikisource.** Works by authors who died by 1965 (death years from Wikidata): Premchand, Jaishankar Prasad, Nirala, Devaki Nandan Khatri, Bharatendu, Ramchandra Shukla, Tulsidas and others. A novel is one title (its chapters are joined in order when the summary is made); a collection whose stories are listed one by one gives one title per story. Translations are left out, because a translator has rights of their own.
- **NPTEL is CC BY-SA.** NPTEL licenses its courses CC BY-SA (YouTube can only show "CC BY"), so those lectures carry CC BY-SA and their summaries say they are shared under the same licence.
- **Country rules.** Copyright is national, so each title records where it may be shown (`library.territories`): openly licensed lectures and books everywhere; books in the UK only when every author died 70+ years ago, in the US when Project Gutenberg lists them or the author died before the US cut-off; films in the US only when published before the US cut-off (95 years), and not in the UK (UK film terms run from the deaths of the director, writers and composer). The viewer's country is the one saved on their account, else Cloudflare's `CF-IPCountry` header, else `CINECUT_MARKET` (India by default). The library, title pages, narration and the partner API (`country=` parameter) all follow it; a title that is not cleared returns HTTP 451.

### Version 12: two lecture courses, the Vedas and Vedic maths

- **The Vedas, Explained** (`backend/webapp/course.py`, plan in `docs/vedas_course.md`, 45 lessons in 8 modules). A teacher's
  voice explains while the key words go up on a manuscript-coloured board (the Sanskrit term in Devanagari, its
  transliteration, its meaning); each verse is recited in Sanskrit by the elder voice, shown with its transliteration, then
  read word by word; public-domain pictures are pinned to the board; a soft tanpura plays underneath. Every lesson has an
  "In your life" part (where the teaching turns up today, with facts that can be checked) and ends with a short quiz. Lessons
  are prepared as `output/youtube/_course/<id>.json`.
- **Vedic Maths, Step by Step** (lecture format, plan in `docs/vedic_maths_course.md`, 21 lessons): the sutra, why it works
  (the algebra on the board), worked examples computed by the program, the common mistake, where you will use it, and a
  practice question. The honest history is told plainly: the sutras were published in 1965 and have not been found in any
  known Vedic text.
- **Competitor analysis** (`docs/vedas_competitor_analysis.md`): what the most watched Veda channels do, where they are weak,
  and the ten changes the course makes because of it (question-first titles, word-by-word meaning, life relevance, quizzes,
  Shorts cut from each lesson, trust through sources).

### Version 11.9: stories read aloud, lectures at the chalkboard, books from their own world

- **Stories, read aloud** (`backend/webapp/reading.py`). A public-domain short story read in full, the way an audiobook
  narrator performs it (characters in slightly different voices, no overacting), over paintings with a soft music bed, a
  title card, a card for each part and a few of its lines on screen. English reads the public-domain text exactly as printed
  (obvious scanning slips corrected and listed in the description); Hindi reads a translation checked line by line. A story
  is prepared as `output/youtube/_stories/<id>.json`. First sample: Tagore's "The Cabuliwallah" (1916 translation).
- **Lectures in brief, at the chalkboard** (`backend/webapp/lecture.py`). A short own-words version of a CC BY lecture,
  taught by a teacher's voice while the points are written up in chalk as they are said (heading, points, the key point
  underlined and ticked), the board filmed as if by a camera on a tripod and wiped between parts; no music. The credit line
  and the changes made, as CC BY asks, are on screen and in the description. First sample: Class 12 inorganic chemistry
  (hybridisation, sigma and pi, H₂SO₄, SF₄), with the lecture summary's two errors corrected.
- **Books with pictures from their own world.** A book documentary can be given subjects to search for (for Rani Ketaki:
  Hindola ragamala swing paintings, Indra on Airavata, a Kangra wedding procession); Indian stories only get Indian paintings
  (no Native American "Indian" pictures, no portraits of officers and royals, no battle scenes).
- **No voice spent twice.** Every natural-voice reading is kept (`output/youtube/_voices`, keyed by the exact text, style
  and cast), so a video re-rendered for a visual fix costs no voice quota.
- **Films.** None of the 2,806 films in the library is cleared for India, the US and the UK together (UK film terms run from
  the deaths of the director, writers and composer), and old films draw Content ID claims from restorers; films stay out
  until a film is cleared everywhere and the owner accepts that risk.

### Version 11.8: a cast of voices, music, the original Sanskrit

- **A cast, not one voice.** Each series has its own narrator (Vedas and hymn recitals: the approved Hindi and British voices; classics: a warm female narrator; lectures: a friendly, knowledgeable one), and the words of the work itself are read by a second, older voice in the same take (Gemini two-speaker speech): a quotation that stands on its own in the script goes to that elder voice, and in *Listen* the elder voice reads every verse while the narrator introduces and closes. Hymns whose seer is a woman (Vak, Ghosha, Lopamudra and others) are read by an elder woman's voice.
- **The original Sanskrit.** A Vedas video or a hymn recital opens with the hymn's first mantra recited in Sanskrit by the elder voice, over a gold-framed card that shows the mantra in Devanagari word by word as it is recited, its seer, deity and metre, and for English viewers a scholarly transliteration (IAST). The mantra and the index facts come from Sanskrit Wikisource (`backend/webapp/sanskrit.py`); the recitation is kept, so the English and Hindi videos of a hymn share it.
- **Music under the voice** (`backend/webapp/music.py`, made here, so there is nothing to license): a tanpura drone under the Vedas and the recitals, soft held chords under everything else. Each is a seamless loop made once; the mix sets it about 16 LU under the narration from both measured loudnesses, lowers it a little more while someone speaks, fades it in and out, and levels the whole to YouTube's loudness.
- **Scripts checked against the text.** Every quotation in an English script must be word for word in the public-domain
  source (a test script had joined two verses into one "quotation"); the writer makes up to four drafts and keeps the one
  with the fewest wrong quotations and stock phrases, and a wrong quotation left over is rewritten with the exact line. A
  Rigveda hymn is walked through verse by verse (about fifty words a verse), with its unfamiliar words explained. Sentences
  that are the translator's exact words are put in quotation marks, so the elder voice reads them; Hindi translations keep
  those marks. More machine-sounding patterns are caught ("imagine", "a reminder", "catalyst", "doesn't just", modern
  similes such as a lighthouse), and more Urdu words in Hindi (अक्सर, शायद, बेहद, ज़रिए, काफ़ी, तरह).
- **Pictures from the Vedic world.** A Vedas video looks first for paintings of the hymn's own deity (read from its Sanskrit
  index: अग्निः gives Agni), then for the Vedic gods, sages and the fire sacrifice; later traditions (Kali, Krishna, Rama,
  Ganesha; Jain and Buddhist art), other countries and sad scenes such as funerals are left out.
- **Titles people search for.** A Vedas video's YouTube title ends with the hymn's name and number ("… | Agni Sukta,
  Rigveda 1.1").
- **Light that belongs to the story.** Sparks rise and firelight flickers along the bottom of the frame while fire and the altar are spoken of; long, slow shafts of morning light fall from the corner while the dawn or the sun is; the title card, the Sanskrit card and the verses sit in a thin gold double frame that draws itself outwards.

### Version 11.7: videos that sound and look made by people

- **Documentary scripts** (`backend/webapp/storyteller.py`). Each YouTube video gets its own script, written from the finished summary and, where the source is on Wikisource, from the public-domain text itself: a cold open on a moment or question from the work (no greeting, no "in this video"), chapters that hand over like one story, specific names and details, two or three short real quotations (put into simple Hindi for Hindi videos), scholars' disagreements said plainly, and an ending on one thought instead of a recap. A list of stock phrases that give machine-written text away ("delve", "tapestry", "essence", "points to remember", "not just X but Y" and more, in both languages) is checked after writing and those sentences are rewritten. Scripts are cached in `output/youtube/_scripts`.
- **Natural voices** (`backend/webapp/yt_voice.py`). English in a British (default) or American voice, Hindi in a male (default) or female voice, chosen on the `/youtube` page. The most natural engine is Gemini speech, told how to talk (a documentary presenter, a radio storyteller, a friendly teacher); it reads a whole video in one or a few continuous takes, which are cut at the pauses between chapters, so the delivery is one performance. Gemini's free speech quota is 10 requests a day per model per key (about 60 a day with three keys, enough for several videos a day). When it is used up, the whole video is voiced with the next engine (Indic Parler-TTS on this PC for Hindi, then Edge), never a mix. The takes are cut into chapters (and maths steps are timed) from a word-by-word transcription of the take itself (Groq Whisper, free), lined up with the script letter by letter, so a cut never falls inside a sentence; the pauses are the fallback. Numbers in Hindi are written out as Hindi words before speaking (some voices misread digits inside Hindi). Every take is checked before it is used: its last 25 seconds are transcribed on their own and must contain the final sentence (one voice once stopped early and left a lesson's practice answer silent); a short take is asked for again, then read as two smaller takes. Batch jobs use natural voices only and wait for the free quota instead of falling back.
- **Documentary pictures.** No slides or bullet points: a title card after the cold open, a card for each chapter, quotations on screen while they are spoken, slow pans and pushes across public-domain paintings with dissolves between shots (a new shot every chapter and about every 26 seconds), light film grain and levelled sound. Each pack has `transcript.txt` for YouTube subtitles (Studio times it to the voice).
- **Maths at the chalkboard.** Lessons are written on a chalkboard in handwriting as the teacher says each step, the answer is underlined and ticked in yellow chalk, and the wording sounds like a teacher ("Right, let's start with 39 times 18.").
- **Pure Hindi, native accent** (after the first Hindi test was judged poor). Hindi scripts and lessons are written in pure, standard Hindi, the way Doordarshan and Akashvani narrators speak (विधि, उत्तर, अंतिम, अधिक, भाग, सरल; no English words and no Urdu words where a Hindi word exists); a list of such words is checked after writing and those sentences are rewritten with the grammar adjusted. The Hindi voice is Gemini told to speak as a native North-Indian narrator, with Microsoft's native Hindi voice as the fallback; Indic Parler-TTS is no longer used for YouTube (its accent was judged poor and it misread digits).
- **Hindi Vedas scripts are translated, not composed.** The free models write English far better than Hindi (a Hindi script written directly had invented words, broken quotations and wrong terms such as पुजारी and बलि), so the Hindi script of a Vedas video is translated from its checked English script into pure, natural Hindi with the right Vedic terms (पुरोहित, होता, यज्ञ, ऋत, ऋषि, सूक्त); both versions then tell the same story. Every Hindi script also gets a last proofreading pass for grammar, gender agreement and spelling, kept only when it changes little and adds no loan word. The machine-like "not merely X, but Y" construction is removed by rule in both languages (केवल … नहीं, बल्कि/अपितु; isn't X, but Y), with no model involved.
- **More life on screen.** Documentaries: soft motes of dust drift across the paintings, and the title, chapter names and quotations appear word by word. Lessons: a piece of chalk travels along each line as it is written and a little chalk dust falls when the line is done.
- **Two more series.** *Listen* (`make_recital`): a Rigveda hymn read in full, each verse on screen while it is spoken, over paintings; English reads Griffith's public-domain words exactly, Hindi reads a checked Hindi translation of them (`storyteller.translate_verses`). *Lectures in brief*: own-words explainers of Creative Commons lectures that allow commercial use (never NPTEL, NonCommercial or ShareAlike), over a moving gradient.
- **Only truly public-domain text is read or quoted.** A Wikisource page that is a modern revision of a translation (for example Rigveda 10.34, "Translation revised 2006 …", under Wikisource's share-alike licence) is never quoted or read aloud, and neither is text with editors' bracketed insertions; the own-words explanation of such a work is still made.
- **The factory** (`factory.py` in the working notes): makes every video that can go on YouTube, in Hindi and English, most valuable first (Vedas, maths lessons, hymn recitals, classics, lectures), with natural voices only; it waits whenever Gemini's free voice quota is used up and carries on as it returns, so about 170 videos arrive over several days. Films are not included: none of the ready films is cleared in the UK, and film footage draws Content ID claims even when public domain.
- **Honest settings.** When a pack uses a realistic AI voice its settings say "Altered or synthetic content: Yes", as YouTube asks; the description keeps one short line about AI tools.

### Version 11.6: Hindi and English, animated, several examples per lesson

- **Every YouTube video in Hindi and English.** Each pack is made in one language (the words on screen differ too); the `/youtube` page makes Hindi, English or both. English long videos use the English summary (a finished Hindi summary is translated first when there is none) and the Indian English voice `en_in_prabhat`; titles, descriptions, chapters, tags and settings are written in the video's language.
- **Maths lessons, not single tricks.** Each Short now teaches one method: the rule in plain words, three worked examples (each on a clean board, every step moving in as it is spoken, the answer with a burst), then a practice problem ("your turn", a 3-2-1 countdown, then the answer). About 1.5 to 2.5 minutes, inside YouTube's 3-minute limit for Shorts (an example is dropped if a lesson would run over). All four problems in a lesson are different and every one is checked by code; the same lesson is made in both languages.
- **Animation everywhere.** Shorts are animated whiteboards over a moving gradient with drifting shapes and a progress bar. Long videos run their slides (headings slide in, points rise into place, the title scales in, a progress bar) over a slow pan and zoom across public-domain paintings from Wikimedia Commons (`backend/webapp/artwork.py`: public domain or CC0 only, at least 1,200 pixels wide, each painting credited in the description; for the Vedas only Indian scenes of gods, sages and rivers, never portraits or court scenes), or over a moving gradient when none suits. A soft shade, darkest behind the words and fading to the right, keeps the text readable. The thumbnail uses the first painting. A script that already ends with its own recap (any wording) is not given a second one.

### Version 11.5: YouTube packs, Hindi first (http://localhost:8080/youtube)

- **What a pack is.** A finished video, its thumbnail, and everything to paste into YouTube Studio: title, description (with chapters, the credit line, the licence and the made-with-AI note), tags and the settings to choose (Education, Hindi, not made for kids, altered or synthetic content: no). Packs wait on the `/youtube` page (this PC only) until you watch and approve them; you publish from YouTube Studio and paste the link back. Nothing is uploaded from CineCut: videos uploaded through the YouTube API stay private until Google audits the app.
- **Three series** (`backend/webapp/youtube.py`):
  - *हिंदी में क्लासिक्स*: own-words Hindi explainers of books and stories, filmed from the finished summary as 16:9 slides with narration (chapters from the sections).
  - *वेद और उपनिषद*: the 1,028 Rigveda hymns (Griffith's translation) and the Upanishads (Max Müller's *Sacred Books of the East*) from English Wikisource (`backend/webapp/sources_vedas.py`); the best-known hymns first. Each description names the translator and says that traditional and scholarly readings differ.
  - *गणित शॉर्टकट*: maths-trick Shorts (1080x1920; since 11.6 full lessons of up to three minutes) (`backend/webapp/lessons.py`). Every example is worked out and checked by code, never by an AI model; the narration is fixed Hindi wording around the checked numbers. The Vedic-maths descriptions say the methods come from Bharati Krishna Tirtha's 1965 book, not from the Vedas.
- **Rules.** YouTube shows a video everywhere, so only titles free in India, the US and the UK are used; never film clips (Content ID claims even public-domain films); never NPTEL (its share-alike licence does not fit YouTube's licence options). If too few titles have a Hindi summary yet, the builder is asked to make them first.
- **Fixes found on the way.** Letter spacing broke Devanagari in the slide and Short headings (it pulled vowel signs apart); every Hindi heading now has no extra spacing. A script that already ends with its own takeaways is not given a second one. The YouTube lecture licence check reads the licence even when yt-dlp cannot choose a playable format (`--ignore-no-formats-error`).

### Version 11.4: more keys, several builders at once

- **More AI providers.** OpenRouter joins Gemini, Groq, NVIDIA and Cloudflare (`OPENROUTER_API_KEY`, `OPENROUTER_API_KEY_2`, ...). Only OpenRouter's free models are used (ids ending in `:free`, with a price cap of zero), so an account that holds credit never spends it; the free allowance is 20 requests a minute and 1,000 a day per account. Cerebras and SambaNova keys are not used: both now ask for a payment method before they answer.
- **Keys are tried again.** Every half hour the builder sends one tiny request to each key and model that is resting; the ones that answer are used again at once (`backend/video_engine/key_check.py`, `llm_usage.clear`). OpenRouter's "per-day" limit replies are recognised as daily limits.
- **Retired models are skipped, not fatal.** NVIDIA's list now starts with its current free models (Nemotron 3 Super, Gemma 4, DeepSeek V4 Flash); a retired model (HTTP 410) or one a Gemini key can no longer use ("no longer available to new users") is skipped and the key's next model is used.
- **Several builders at once.** `CINECUT_BUILDER_WORKERS` (default 3) summary workers write books, stories and lectures side by side, sharing the keys; one more worker handles films on the GPU. Each title is taken by one worker only.

### Version 11.3: viewers in the US and the UK

- **English versions.** A finished summary is translated (`backend/webapp/translate.py`: the script's headings, points and narration, or a film's narration lines) instead of being made again from the source, so it costs a few AI requests. The builder adds English to titles cleared in the US or the UK and Hindi to US and UK sources cleared in India, one title every third turn and right after each new summary. Viewers in India hear Hindi first and everyone else English (`library.preferred_language`), and can switch.
- **US and UK sources** (`backend/webapp/sources_intl.py`, part of `catalog.harvest()`): US Supreme Court opinions from English Wikisource (court opinions carry no copyright; shelved for US viewers; the Court's own opinion is read before concurrences and dissents), GOV.UK guides and detailed guides under the Open Government Licence v3.0 (UK shelf, with the licence's attribution statement), and NASA videos that have caption files (everywhere, "Courtesy NASA", no suggestion of endorsement; summarised from the captions, so no transcription allowance is used). Films and episodes whose Wikidata item records why they are public domain in the United States (copyright not renewed, no notice, a federal work) are shown to US viewers too.
- **Summary length follows the source**: about a third of the original's reading time, between 2 minutes and the shelf's usual length. The builder spends one round in four on titles cleared only outside the home market (`CINECUT_MARKET`), so the US and UK shelves grow while India stays first.
- **Prices in dollars and pounds.** US viewers see US dollars and UK viewers pounds (UK prices include VAT; US sales tax is added by Stripe Tax at checkout). Stripe Checkout takes over when `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` are set and `CINECUT_PAYMENTS_FREE=0`; the webhook (`POST /api/app/stripe/webhook`) checks the Stripe-Signature HMAC and its timestamp, and an order is paid and its plan started exactly once. Orders record their currency. A paid mode with no provider set up refuses orders instead of giving plans away.
- **Fixes from the retest.** The library index rescans in a background thread, so a harvest writing thousands of files never slows the web app (only the first load after a start waits). Hindi titles get readable addresses (`godana`, not `title`). A title that is not cleared for the viewer's country says so and offers a way back or a change of country. The Terms explain the country rules and the licences of NPTEL, GOV.UK, NASA and court opinions.
- **Retest**: scratchpad `v112_test.py` (40 checks across the library, country rules, partner API, languages, prices, the Stripe webhook and the pages the browser loads) plus the v10, v10b, v11 and voice-removal suites.
- **Fixes from the full retest.** The library now lives in one SQLite database (`data/library.db`, WAL mode) instead of one JSON file per title: at 75,000 titles, opening that many small files on Windows took over ten minutes, while the database loads in seconds and other processes' changes are picked up every few seconds without anyone waiting (the old folder is kept as `data/library_json_backup`). The server starts in seconds (the builder no longer counts the library at start). The end card no longer fails a render when a long file name is cut at a space (its work folder is named by a hash, and the card can never fail the render). The start-up clean-up of private viewing only deletes private work left by processes that have ended, so a restart no longer deletes a private job another process is still working on. LibriVox recordings found through the Internet Archive are cleared when LibriVox's author list shows the author died 60+ years ago (Conan Doyle, 1930).
- **Fast listings at 75,000 titles.** Each title's country clearance is worked out once per version of the title, and the listing and shelf counts for a country are shared by every viewer for 10 seconds (which also keeps "Show more" pages in one order). On a quiet server the library page answers in about 7 ms from the shared listing and 0.3 s when it is worked out again; before this it took 1.4 s.
- **One voice per title when Sarvam runs out.** A "no credits" answer from Sarvam (HTTP 402) rests the Sarvam voice for 6 hours and a rate limit for a minute; while it rests every title, explainer and dub picks the free Edge voice from the start, instead of trying Sarvam line by line and mixing two voices in one title.
- **Library after the full harvest**: about 75,600 titles; 59,600 cleared for India, 61,100 for the US, 64,200 for the UK (films are not shelved in the UK, GOV.UK guides only there, US court opinions only in the US).

## Version 10: copyright-safe by design

- **Terms of Use.** On first start CineCut shows the rules and asks you to accept the Terms (`/terms`). Until the current version is accepted, downloads, uploads and analysis are refused. Fill these in `.env` before offering CineCut to anyone else, and have a lawyer review the Terms: `CINECUT_OPERATOR`, `CINECUT_CONTACT_EMAIL`, `CINECUT_GRIEVANCE_OFFICER`, `CINECUT_JURISDICTION_CITY`. `CINECUT_COUNTRY` (default `IN`) sets the public-domain rules; `CINECUT_PRIVATE_TTL_MIN` (default 60) sets how long private summaries live.
- **Rights check before anything is downloaded.** CineCut reads the licence at the source (YouTube's and Vimeo's licence field, the Internet Archive's licence, Wikimedia Commons, Project Gutenberg's author and translator death years) and gives one of four results:
  - *Cleared*: public domain in your country, or a licence that allows adaptations (CC0, CC BY, CC BY-SA; CC BY-NC for non-commercial use). Every feature works; credit lines are added.
  - *Declared*: you state that you made it or have written permission. Every feature works; the declaration is logged in `data/rights_declarations.jsonl`.
  - *Private viewing*: no verified licence, or a NoDerivatives licence. The summary is shown in the app only: downloads, exports and publishing packs are off, the shared caches are not written, and everything made for it (including a source CineCut downloaded) is deleted when you press "Delete now", 60 minutes after it was last opened, or when CineCut starts. Files you pointed to on your own disk are never deleted.
  - *Blocked*: DRM streaming services (Netflix, Prime Video, Hotstar and others). Use the watch guide instead.
  India protects films for 60 years from publication and books for 60 years after the author (and any translator) dies, so a film or book that is public domain in the USA can still be private here. Re-uploads of commercial films that carry a Creative Commons badge are treated as private.
- **Free library** tab: searches the Internet Archive, Wikimedia Commons, YouTube (Creative Commons only), Project Gutenberg and LibriVox, and shows each result's status for your country.
- **Own-words summaries** (the "Book / Text" tab, or "Own-words summary" in the studio): CineCut reads a book, lecture transcript or story in parts, then as a whole, and retells its essence in new words with its own slides and narration, a glossary, takeaways and a short quiz. Each sentence is compared with the source; any that repeat its wording are rewritten. The last card credits the source and says the explainer was made with AI. Sources: Project Gutenberg books, pasted text, `.txt`/`.md`/`.pdf`/`.srt` files, or the transcript of a loaded video.

## Version 9: sharper analysis, better voices, quality score

- **Shots and sounds:** with PyTorch installed, shots are found by TransNetV2 on the GPU (it also catches fades and dissolves) and every 2 seconds of audio is tagged by PANNs (music, singing, laughter, applause, explosions, gunfire, screams, crying). The Songs, Action, Comedy and Emotional filters use these. Without PyTorch the old FFmpeg rules are used.
- **The AI looks at scenes:** for songs, fights and silent moments, small low-resolution clips go to Gemini's free tier. What it sees is used by the story finder, the filters and the narration. Turn it off with the "AI looks at scenes" switch.
- **Stable story beats:** the film is read in 12-minute parts, then the beats are chosen from all events, about one beat per 7 minutes of film.
- **Narration:** lines for all cut-out parts are written a few requests at a time. Each line is spoken in a natural pause of its clip; when the clip starts with dialogue straight away, it is spoken over a still of the clip's first frame instead of over the dialogue.
- **Voices:** the default Hindi and English narrator is Gemini's expressive speech (free tier). If its quota runs out, the whole narration switches to the matching Edge voice so one film never mixes voices. Sarvam Bulbul voices appear when `SARVAM_API_KEY` is in `.env`. AI4Bharat Indic Parler-TTS runs on the laptop once you accept its licence on Hugging Face, add `HF_TOKEN` to `.env` and run `python -m pip install parler-tts transformers`.
- **Reel captions:** karaoke (words light up as spoken), pop (the spoken word grows), plain or classic. Word timings come from the Groq transcript when there is one.
- **Quality score:** every cut gets a 0-100 score (story beats kept, no huge skipped stretch, clips ending between lines, length, narration). "Check with AI" has an AI watch the cut like a first-time viewer; "Fix weak spots" adds the scenes where it would get lost and keeps your length.

## Security

The server only accepts requests addressed to this PC and blocks cross-site requests from other web pages. With `--lan`, other devices can reach only the TV player. File-serving endpoints reject paths that try to leave their folder.
