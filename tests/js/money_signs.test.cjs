// The pages show a negative amount as the printed documents do (2.18.0): a
// discount line reads "-$10.00", never "$-10.00". formatCurrency (Intl) and
// SalesLines, which the invoice and sales receipt views use for every line
// price and amount, put the minus first; a foreign document keeps its code
// in front ("EUR -10.00"), as its PDF does.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load() {
    const context = { window: {}, App: { settings: {} }, document: { addEventListener() {} } };
    vm.createContext(context);
    const js = name => fs.readFileSync(path.join(__dirname, '../../app/static/js', name), 'utf8');
    vm.runInContext(js('utils.js') + '\nthis.formatCurrency = formatCurrency;', context);
    vm.runInContext(js('invoices.js') + '\nthis.SalesLines = SalesLines;', context);
    return context;
}

test('a negative amount reads with its minus first', () => {
    const { formatCurrency, SalesLines } = load();
    assert.equal(formatCurrency(-10), '-$10.00');
    assert.equal(SalesLines.money(-10), '-$10.00');
    assert.equal(SalesLines.rate('-10.00'), '-$10.00');
    assert.equal(SalesLines.rate('-0.045'), '-$0.045');
    assert.equal(SalesLines.money(-10, 'EUR'), 'EUR -10.00');
});

test('a positive amount reads as it did', () => {
    const { formatCurrency, SalesLines } = load();
    assert.equal(formatCurrency(1234.5), '$1,234.50');
    assert.equal(SalesLines.rate('0.045'), '$0.045');
    assert.equal(SalesLines.money(850, 'EUR'), 'EUR 850.00');
});
