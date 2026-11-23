"""Isolated CPU speech recognition; invoked with the optional ASR runtime."""
import json
import sys


def main():
    from faster_whisper import WhisperModel
    model=WhisperModel('tiny',device='cpu',compute_type='int8',cpu_threads=1,
                       download_root=sys.argv[2],local_files_only=True)
    segments,info=model.transcribe(sys.argv[1],beam_size=3,vad_filter=True,
                                  condition_on_previous_text=False)
    accepted=[s for s in segments if s.no_speech_prob<.6 and s.avg_logprob>-1]
    print(json.dumps({'text':' '.join(s.text.strip() for s in accepted),
                      'language':info.language},ensure_ascii=False))


if __name__=='__main__':
    main()
