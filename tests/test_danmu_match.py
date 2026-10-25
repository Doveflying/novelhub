import sys, io
sys.path.insert(0, r'C:\Users\韩凌云\Desktop\novel')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from novelhub.webapp.app import _levenshtein, _pick_best_anime, _pick_best_episode

ok = fail = 0


def check(name, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f'  PASS  {name}')
    else:
        fail += 1
        print(f'  FAIL  {name}  得到 {got!r} 期望 {want!r}')


print('=== _levenshtein ===')
check('相同', _levenshtein('abc', 'abc'), 0)
check('一字差', _levenshtein('abc', 'abd'), 1)
check('空串', _levenshtein('', 'abc'), 3)
check('中文', _levenshtein('进击的巨人', '进击的巨人'), 0)
check('中文多字', _levenshtein('进击的巨人', '进击的巨人 最终季'), 4)

print()
print('=== _pick_best_anime：不能被前缀更长的那部骗走 ===')
cands = [
    {'animeTitle': '进击的巨人 最终季'},
    {'animeTitle': '进击的巨人'},
    {'animeTitle': '进击的巨人 第三季'},
]
got = _pick_best_anime(cands, '进击的巨人')
check('精确匹配', got.get('animeTitle'), '进击的巨人')

cands2 = [
    {'animeTitle': '进击的巨人 最终季'},
    {'animeTitle': '进击的巨人 第三季'},
]
got = _pick_best_anime(cands2, '进击的巨人')
check('无精确时取最近', got.get('animeTitle') in
      ('进击的巨人 第三季', '进击的巨人 最终季'), True)

print()
print('=== _pick_best_episode：集号优先 ===')
eps = [
    {'episodeId': 1, 'episodeTitle': '第1话'},
    {'episodeId': 2, 'episodeTitle': '第2话'},
    {'episodeId': 3, 'episodeTitle': '第3话'},
]
check('第2话', _pick_best_episode(eps, 2), 1)
check('第3话', _pick_best_episode(eps, 3), 2)

print()
print('=== 混入总集篇时不能错位 ===')
eps2 = [
    {'episodeId': 10, 'episodeTitle': '总集篇'},
    {'episodeId': 11, 'episodeTitle': '第1话'},
    {'episodeId': 12, 'episodeTitle': '第2话'},
]
check('要第1话（不是总集篇）', _pick_best_episode(eps2, 1), 1)
check('要第2话', _pick_best_episode(eps2, 2), 2)

print()
print('=== 集名提示兜底 ===')
eps3 = [
    {'episodeId': 20, 'episodeTitle': 'A さん'},
    {'episodeId': 21, 'episodeTitle': 'B さん'},
]
check('按名字找 B', _pick_best_episode(eps3, 99, 'B さん'), 1)

print()
print('=== 越界夹住 ===')
check('要第99集只有3集', _pick_best_episode(eps, 99), 2)

print()
print(f'通过 {ok}，失败 {fail}')
sys.exit(1 if fail else 0)
