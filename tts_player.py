import os
import uuid
import asyncio
import subprocess
import edge_tts

def play_audio_file(file_path: str):
    """Plays audio via Windows PowerShell Media Player, waiting for full playback duration."""
    abs_path = os.path.abspath(file_path).replace("'", "''")
    try:
        ps_script = f"""
        Add-Type -AssemblyName PresentationCore
        $p = New-Object System.Windows.Media.MediaPlayer
        $p.Open([System.Uri]'{abs_path}')
        
        # Wait up to 2 seconds for media metadata/duration to load
        $waited = 0
        while ((-not $p.NaturalDuration.HasTimeSpan) -and ($waited -lt 25)) {{
            Start-Sleep -Milliseconds 100
            $waited++
        }}
        
        $p.Play()
        
        if ($p.NaturalDuration.HasTimeSpan) {{
            $totalMs = [int]$p.NaturalDuration.TimeSpan.TotalMilliseconds
            Start-Sleep -Milliseconds ($totalMs + 500)
        }} else {{
            # Fallback wait if metadata unavailable
            Start-Sleep -Seconds 6
        }}
        $p.Close()
        """
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60
        )
        return True
    except Exception:
        return False

async def speak_text_aloud(text: str, voice: str = "en-US-ChristopherNeural"):
    """Synthesizes speech and plays it out loud safely without crashing."""
    if not text:
        return

    temp_file = None
    try:
        communicate = edge_tts.Communicate(text, voice)
        temp_file = f"temp_voice_{uuid.uuid4().hex[:6]}.mp3"
        await communicate.save(temp_file)
        await asyncio.to_thread(play_audio_file, temp_file)
    except Exception as e:
        # Never crash if speaker playback has driver issues
        pass
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass

if __name__ == "__main__":
    print("Testing speech synthesis...", flush=True)
    asyncio.run(speak_text_aloud("Hello! CallBridge audio test."))
    print("Test finished.", flush=True)