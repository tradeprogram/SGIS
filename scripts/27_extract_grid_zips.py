"""
grid_data 폴더의 남은 zip 압축 해제 후 zip 삭제.

한글 파일명 주의: 국내 배포 zip은 UTF-8 플래그 없이 CP949로 이름이 들어있는 경우가 많다.
zipfile은 플래그가 없으면 CP437로 디코딩하므로, 그 경우 cp437 → cp949로 되돌린다.

삭제는 압축 해제가 실제로 성공한 zip에 대해서만 수행한다(파일 수·크기 확인 후).
"""

import os
import zipfile

ROOT = r'C:\for_sgis\data\grid_data'

zips = sorted(f for f in os.listdir(ROOT) if f.lower().endswith('.zip'))
print(f'zip 파일: {len(zips)}개\n')

ok, failed = [], []

for zf in zips:
    zpath = os.path.join(ROOT, zf)
    outdir = os.path.join(ROOT, os.path.splitext(zf)[0])

    try:
        with zipfile.ZipFile(zpath) as z:
            infos = z.infolist()
            n_written = 0
            for info in infos:
                name = info.filename
                # UTF-8 플래그(0x800)가 없으면 CP437로 잘못 디코딩된 상태 → CP949로 복원
                if not (info.flag_bits & 0x800):
                    try:
                        name = name.encode('cp437').decode('cp949')
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass

                # 경로 탈출 방지
                name = name.replace('\\', '/').lstrip('/')
                if '..' in name.split('/'):
                    print(f'  [건너뜀] 의심 경로: {name}')
                    continue

                target = os.path.join(outdir, *name.split('/'))
                if info.is_dir() or name.endswith('/'):
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as src, open(target, 'wb') as dst:
                    dst.write(src.read())
                n_written += 1

        # 검증: 실제로 파일이 나왔고 총 크기가 0보다 큰지
        total = 0
        for dirpath, _, files in os.walk(outdir):
            for f in files:
                total += os.path.getsize(os.path.join(dirpath, f))

        if n_written > 0 and total > 0:
            ok.append((zf, n_written, total))
            print(f'  [해제] {zf}  → {n_written}개 파일, {total/1e6:.2f} MB')
        else:
            failed.append((zf, '해제 후 내용 없음'))
            print(f'  [실패] {zf}: 해제 후 내용 없음 — zip 보존')

    except Exception as e:
        failed.append((zf, str(e)))
        print(f'  [실패] {zf}: {e} — zip 보존')

print(f'\n해제 성공 {len(ok)}개 / 실패 {len(failed)}개')

# 성공한 것만 삭제
n_del = 0
for zf, _, _ in ok:
    os.remove(os.path.join(ROOT, zf))
    n_del += 1
print(f'zip 삭제: {n_del}개')

if failed:
    print('\n보존된 zip (수동 확인 필요):')
    for zf, why in failed:
        print(f'  - {zf}: {why}')
