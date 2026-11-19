from . import beat_sync, denoise, setup_ffmpeg, subtitles, voiceover

HANDLERS = {
    "beat_sync": beat_sync.run,
    "denoise": denoise.run,
    "subtitles": subtitles.run,
    "setup_ai": subtitles.setup,
    "setup_ffmpeg": setup_ffmpeg.run,
    "voiceover": voiceover.run,
    "setup_tts": voiceover.setup,
}
