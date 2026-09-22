const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');

test('OCR loads only on demand, shares in-flight loading and can retry failure', async () => {
    const scripts = [];
    const context = vm.createContext({
        window: {}, setTimeout, clearTimeout,
        document: {
            createElement: () => ({ remove() {} }),
            head: { appendChild: script => scripts.push(script) }
        }
    });
    vm.runInContext(fs.readFileSync('static/js/ocr_extract.js', 'utf8'), context);
    assert.equal(scripts.length, 0);
    const first = context.loadOCREngine();
    assert.equal(context.loadOCREngine(), first);
    assert.equal(scripts.length, 1);
    scripts[0].onerror();
    await assert.rejects(first);
    const retry = context.loadOCREngine();
    assert.equal(scripts.length, 2);
    context.window.Tesseract = { recognize() {} };
    scripts[1].onload();
    assert.equal(await retry, context.window.Tesseract);
    assert.equal(await context.loadOCREngine(), context.window.Tesseract);
    assert.equal(scripts.length, 2);
});
