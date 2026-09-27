// An export result's notes show on the QuickBooks Online page (2.18.0): what
// went to QBO differently from how it reads here, and why (an invoice's
// discounts on several accounts go as QBO's one discount on a transaction).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const NOTE = 'Invoice 2004 went to QuickBooks Online with one discount of 14.00 on Discounts given (QBO #86), as QuickBooks Online takes one discount on a transaction. It includes 4.00 on Promotions (QBO #87).';

function page(fetchBody) {
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, { innerHTML: '', querySelectorAll: () => [] });
        return elements.get(id);
    };
    const context = {
        console, window: {},
        App: { setStatus() {} }, toast() {},
        API: { post: async () => fetchBody, responseError: async () => 'failed' },
        fetch: async () => ({ ok: true, status: 200, json: async () => fetchBody }),
        $: selector => element(selector), $$: () => [],
        escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]),
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/qbo.js'), 'utf8') + '\nthis.QBOPage = QBOPage;', context);
    const qbo = context.QBOPage;
    qbo._getChecked = () => ['invoices'];
    return { qbo, element };
}

const warnings = html => html.match(/<div class="iif-warnings">([\s\S]*?)<\/div>/)?.[1];

for (const [name, run] of [
    ['Export All', f => f.qbo.exportAll()],
    ['Export Selected', f => f.qbo.exportSelected()],
]) {
    test(`${name} shows the export's notes`, async () => {
        const f = page({ invoices: 1, exported: 1, errors: [], notes: [{ entity: 'invoice', id: 7, message: NOTE }] });
        await run(f);
        assert.equal(warnings(f.element('#qbo-export-result').innerHTML), `${NOTE}<br>`);
    });
}

test('an export with nothing to note shows no note box', async () => {
    const f = page({ invoices: 1, exported: 1, errors: [], notes: [] });
    await f.qbo.exportSelected();
    assert.equal(warnings(f.element('#qbo-export-result').innerHTML), undefined);
});

// The export brings records it sent before up to date (2.18.0): the page
// counts what it updated and voided in QuickBooks Online, and the sales
// receipts the invoices step sent, apart from the invoices.
const rows = html => [...html.matchAll(/<span>([^<]*)<\/span>\s*<span class="result-count">([^<]*)<\/span>/g)].map(m => [m[1], m[2].trim()]);

test('Export Selected counts updates, voids and sales receipts', async () => {
    const f = page({ exported: 3, sales_receipts: 1, updated: 2, voided: 1, errors: [], notes: [] });
    await f.qbo.exportSelected();
    assert.deepEqual(rows(f.element('#qbo-export-result').innerHTML), [
        ['Invoices', '2 exported'],
        ['Sales Receipts', '1 exported'],
        ['Updated in QuickBooks Online', '2'],
        ['Voided in QuickBooks Online', '1'],
    ]);
});

test('Export All shows what it brought up to date', async () => {
    const f = page({ invoices: 0, updated: 1, voided: 0, errors: [], notes: [] });
    await f.qbo.exportAll();
    assert.deepEqual(rows(f.element('#qbo-export-result').innerHTML), [['Updated in QuickBooks Online', '1']]);
});
