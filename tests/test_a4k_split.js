/* 校验 anime4k.js 的切趟逻辑：趟数、SAVE 名、以及每趟用到的宏是否都被模板提供。
   只校验实际启用的 Anime4K_Restore_CNN_S —— 那是 PERFORMANCE 档唯一用到的文件。 */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', 'novelhub', 'webapp', 'static');

const src = fs.readFileSync(path.join(ROOT, 'anime4k.js'), 'utf8');
const grab = (name) => {
  const i = src.indexOf('function ' + name);
  if (i < 0) throw new Error('找不到 ' + name);
  let depth = 0, started = false, j = i;
  for (; j < src.length; j++) {
    if (src[j] === '{') { depth++; started = true; }
    else if (src[j] === '}') { depth--; if (started && depth === 0) { j++; break; } }
  }
  return src.slice(i, j);
};

const splitPasses = eval('(' + grab('splitPasses') + ')');
const fragTemplate = eval('(' + grab('fragTemplate') + ')');

let fail = 0;

function check(file, expectPasses) {
  const text = fs.readFileSync(path.join(ROOT, 'shaders', file), 'utf8');
  const passes = splitPasses(text);
  console.log('\n=== ' + file + '  ->  ' + passes.length + ' 趟');

  if (passes.length !== expectPasses) {
    console.log(`  FAIL 趟数 ${passes.length}，期望 ${expectPasses}`);
    fail++;
  }

  passes.forEach((ps, i) => {
    const macro = ps.binds.filter(b => b !== 'MAIN')[0] || ps.binds[0] || 'MAIN';

    /* 模板里实际提供了哪些宏 */
    const tpl = fragTemplate(macro);
    const provided = new Set(['MAIN']);
    for (const m of tpl.matchAll(/#define\s+(\w+)_(?:texOff|tex|pos)/g)) {
      provided.add(m[1]);
    }

    /* 卷积体实际用到了哪些宏 */
    const used = new Set();
    for (const m of ps.body.matchAll(/\b(\w+)_(?:texOff|tex)\s*\(/g)) used.add(m[1]);

    const missing = [...used].filter(n => !provided.has(n));
    const hasHook = /vec4\s+hook\s*\(/.test(ps.body);
    const ok = hasHook && missing.length === 0;
    if (!ok) fail++;

    console.log(`  [${i}] save=${(ps.save || '-').padEnd(14)} ` +
                `binds=[${ps.binds.join(',')}]`);
    console.log(`      macro=${macro.padEnd(14)} 用=[${[...used].join(',')}] ` +
                `hook=${hasHook} ${missing.length ? 'FAIL 缺宏:' + missing : 'OK'}`);
  });
}

check('Anime4K_Restore_CNN_S.glsl', 4);

console.log('\n失败项: ' + fail);
process.exit(fail ? 1 : 0);
