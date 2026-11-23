import json, subprocess
m = dict(json.load(open('demo/marks.json')))
segs = [('clip_home', 'home', 'zoom'), ('clip_map', 'zoom', 'val'), ('clip_val', 'val', 'heat'), ('clip_market', 'heat', 'end')]
def ff(*a): subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', *a], check=True)
enc = ['-c:v', 'libx264', '-preset', 'slow', '-crf', '24', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', '-r', '30']
for name, a, z in segs:
    s, e = m[a] - 0.3, m[z]
    ff('-ss', str(s), '-to', str(e), '-i', 'demo/raw.webm', '-vf', 'scale=780:1688', *enc, f'demo/{name}.mp4')
    ff('-ss', str(min(e - 0.5, s + (e - s) * 0.6)), '-i', f'demo/raw.webm', '-frames:v', '1', '-vf', 'scale=780:1688', f'demo/{name}.png')
ff('-ss', str(m['home'] - 0.3), '-to', str(m['end']), '-i', 'demo/raw.webm', '-vf', 'scale=780:1688', *enc, 'demo/demo_full.mp4')
