import json, subprocess, re
m = json.load(open('meta.json')); st = m['starts']
out = subprocess.run(['ffmpeg', '-i', m['video'], '-vf', 'fps=50,crop=6:6:0:0,signalstats,metadata=print:key=lavfi.signalstats.VAVG', '-f', 'null', '-'],
                     capture_output=True, text=True).stderr
vs = [float(x) for x in re.findall(r'VAVG=([\d.]+)', out)]
hits = [i / 50 for i, v in enumerate(vs) if v > 170]
groups = []
for h in hits:
    if not groups or h - groups[-1][-1] > 0.5: groups.append([h])
    else: groups[-1].append(h)
vt = [g[0] for g in groups]
print('video marker times', [round(x, 2) for x in vt], 'expected', [round(x, 2) for x in st])
assert len(vt) == 4, 'marker detection failed'
# piecewise alignment: scene i starts (video vt[i]) must land on audio start st[i]
end_v = vt[3] + (m['total'] - st[3]) * ((vt[3] - vt[2]) / (st[3] - st[2]))
vb = vt + [end_v]; sb = st + [m['total']]
parts = []
for i in range(4):
    k = (sb[i + 1] - sb[i]) / (vb[i + 1] - vb[i])
    print(f'scene {i+1}: video speed x{1/k:.2f}')
    parts.append(f'[0:v]trim={vb[i]:.3f}:{vb[i+1]:.3f},setpts=(PTS-STARTPTS)*{k:.5f}[v{i}]')
vf = ';'.join(parts) + ';[v0][v1][v2][v3]concat=n=4:v=1:a=0,crop=1280:712:0:8,scale=1280:720[vout]'
inputs = []; fl = [vf]
for i, s in enumerate(st):
    inputs += ['-i', f's{i+1}.mp3']; ms = int(s * 1000)
    fl.append(f'[{i+1}:a]adelay={ms}|{ms}[a{i}]')
fl.append(''.join(f'[a{i}]' for i in range(4)) + 'amix=inputs=4:duration=longest,volume=6[aout]')
cmd = ['ffmpeg', '-y', '-i', m['video']] + inputs + ['-filter_complex', ';'.join(fl), '-map', '[vout]', '-map', '[aout]', '-t', str(m['total']),
       '-r', '30', '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k', 'urdu_test_15s.mp4']
r = subprocess.run(cmd, capture_output=True, text=True); print('ffmpeg', r.returncode, r.stderr[-300:] if r.returncode else '')
for i, t in enumerate([2.0, 5.3, 8.6, 13.4]):
    subprocess.run(['ffmpeg', '-y', '-ss', str(t), '-i', 'urdu_test_15s.mp4', '-frames:v', '1', '-vf', 'scale=800:-1', f'f{i+1}.png'], capture_output=True)
