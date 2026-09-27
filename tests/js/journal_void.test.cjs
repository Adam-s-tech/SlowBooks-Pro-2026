// The Journal Entries page offers Void on a journal the QuickBooks Online
// import posted (2.18.0: it voids here like any journal), and the entry
// view offers it too, so a posting opened from the bank register (a QBO
// deposit) can be voided there. A document's own posting voids from its
// document, so its view offers no Void.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function page(entry) {
    const dialogs = [];
    const context = {
        API: { get: async () => entry },
        escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[character]),
        formatDate: value => value, formatCurrency: value => `$${value}`,
        CostCodes: { headHtml: () => '' },
        openModal: (title, html) => dialogs.push({ title, html }),
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/journal.js'), 'utf8') + '\nthis.JournalPage = JournalPage;', context);
    return { page: context.JournalPage, dialogs };
}

const entry = source_type => ({
    id: 7, date: '2026-08-03', description: 'QBO Deposit', reference: '', source_type,
    lines: [], total_debit: 20, total_credit: 20,
});

for (const kind of ['manual', 'qbo_journal', 'qbo_ledger']) {
    test(`a ${kind} entry offers Void, in the list and in its view`, async () => {
        const f = page(entry(kind));
        assert.equal(f.page.canVoid(entry(kind)), true);
        await f.page.view(7);
        assert.match(f.dialogs[0].html, /onclick="JournalPage\.void\(7\)"/);
    });
}

for (const kind of ['manual_void', 'qbo_ledger_void', 'bill_payment', 'deposit']) {
    test(`a ${kind} entry does not`, async () => {
        const f = page(entry(kind));
        assert.equal(f.page.canVoid(entry(kind)), false);
        await f.page.view(7);
        assert.doesNotMatch(f.dialogs[0].html, /JournalPage\.void/);
    });
}
