import json, subprocess, os
m = dict(json.load(open('demo/marks.json'))); fr = json.load(open('demo/frames.json'))
def ff(*a): subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', *a], check=True)
enc = ['-c:v', 'libx264', '-preset', 'slow', '-crf', '22', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an']
def make(name, a, z):
    s, e = m[a] - 0.3, m[z]
    sel = [(n, t) for n, t in fr if s - 2 <= t <= e]
    # 從開始點前的最後一張開始
    pre = [x for x in sel if x[1] <= s]; sel = ([pre[-1]] if pre else []) + [x for x in sel if x[1] > s]
    lines = []
    for i, (n, t) in enumerate(sel):
        t0 = max(t, s); t1 = sel[i + 1][1] if i + 1 < len(sel) else e
        lines.append(f"file 'fr/{n:05d}.jpg'\nduration {max(t1 - t0, 0.001):.4f}")
    lines.append(f"file 'fr/{sel[-1][0]:05d}.jpg'")
    open(f'demo/{name}.txt', 'w').write('\n'.join(lines) + '\n')
    ff('-f', 'concat', '-safe', '0', '-i', f'demo/{name}.txt', '-vf', 'fps=30,scale=780:1688:flags=lanczos', *enc, f'demo/{name}.mp4')
    mid = sel[int(len(sel) * 0.7)][0]; ff('-i', f'demo/fr/{mid:05d}.jpg', f'demo/{name}.png')
for name, a, z in [('clip_home', 'home', 'zoom'), ('clip_map', 'zoom', 'val'), ('clip_val', 'val', 'heat'), ('clip_market', 'heat', 'end')]:
    make(name, a, z)
make('demo_full', 'home', 'end')
