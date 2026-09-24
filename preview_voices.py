"""Play sample lines in each candidate agent voice, so you can pick one.

    python preview_voices.py                 # play the shortlist out loud
    python preview_voices.py --all-english   # every English voice
    python preview_voices.py --save ./voices # write mp3s instead of playing

The app offers Female (en-US-AvaNeural) and Male (en-US-AndrewNeural) as a
dropdown. To use any other voice as the default, set it in .env:

    TTS_VOICE=en-GB-SoniaNeural
"""
import argparse
import asyncio
import sys
from pathlib import Path

import edge_tts

from tts_player import play_audio_file

SAMPLE_LINE = ("Hello, I am calling about early check-in for my client. "
               "Would twenty dollars work instead?")

# The first two are the options the app offers as Female and Male; the rest
# are alternatives you can set by hand with TTS_VOICE.
SHORTLIST = [
    "en-US-AvaNeural", "en-US-AndrewNeural",
    "en-US-EmmaNeural", "en-US-BrianNeural",
    "en-US-JennyNeural", "en-US-ChristopherNeural",
    "en-GB-SoniaNeural", "en-GB-RyanNeural",
]


async def english_voices():
    voices = await edge_tts.list_voices()
    return sorted(voice["ShortName"] for voice in voices
                  if voice["Locale"].startswith(("en-US", "en-GB", "en-AU", "en-IE")))


async def render(voice, text):
    audio = b""
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    return audio


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all-english", action="store_true", help="preview every English voice, not just the shortlist")
    parser.add_argument("--save", metavar="DIR", help="write mp3 files to DIR instead of playing them")
    parser.add_argument("--text", default=SAMPLE_LINE, help="say something else")
    options = parser.parse_args()

    voices = await english_voices() if options.all_english else SHORTLIST
    destination = Path(options.save) if options.save else None
    if destination:
        destination.mkdir(parents=True, exist_ok=True)

    for voice in voices:
        try:
            audio = await render(voice, options.text)
        except Exception as error:
            print(f"{voice:<32} could not be generated: {error}")
            continue
        target = (destination or Path(".")) / f"{voice}.mp3"
        target.write_bytes(audio)
        if destination:
            print(f"{voice:<32} saved to {target}")
            continue
        print(f"{voice:<32} playing…", flush=True)
        await asyncio.to_thread(play_audio_file, str(target))
        target.unlink(missing_ok=True)

    if not destination:
        print("\nPick one and put it in .env, for example:  TTS_VOICE=en-US-AvaNeural")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
