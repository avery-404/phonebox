const { test, expect } = require('@playwright/test');

// 配額卡有三個圓環（例如 Antigravity）時：每欄很窄、標籤行數也不同。圓環要同大小、頂端對齊，標籤不能超出自己的欄位。
// A quota card with three rings (Antigravity has three): the columns are narrow and their labels differ in length. The rings
// must be the same size and top-aligned, and no label may spill out of its own column.
const NOW = Date.now();
const quotas = {
  providers: [{
    id: 'antigravity', name: 'Antigravity', plan: 'Antigravity', updatedAt: NOW,
    limits: [
      { label: 'Weekly (Gemini)', percent: 26, resetsAt: NOW + 7 * 86400_000 },
      { label: '5 hour (Claude/GPT)', percent: 0, resetsAt: null },
      { label: 'Weekly (Claude/GPT)', percent: 0, resetsAt: null },
    ],
  }],
};

async function load(page, size) {
  await page.setViewportSize(size);
  await page.goto('/panel/style.css');
  await page.evaluate(() => {
    localStorage.clear();
    localStorage.setItem('pb-demo-config', JSON.stringify({ night: { enabled: false }, shortcuts: [], display: { idleReturnSec: 0 },
      pages: [{ id: 'q', title: 'Quotas', widgets: [{ id: 'q1', type: 'quotas', x: 0, y: 0, w: 6, h: 6, options: { providers: ['antigravity'] } }] }] }));
  });
  await page.goto('/panel/');
  await expect(page.locator('.w-quotas')).toHaveCount(1);
  await page.evaluate(async (q) => {
    const { D, emit } = await import('/panel/main.js');
    D.quotas = q;
    emit('quotas');
  }, quotas);
  await expect(page.locator('.q-dial')).toHaveCount(3);
}

for (const size of [{ width: 780, height: 360 }, { width: 750, height: 420 }]) {
  test(`three rings stay aligned and inside their columns at ${size.width}x${size.height}`, async ({ page }) => {
    await load(page, size);
    const boxes = await page.locator('.q-dial').evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return { top: r.top, w: r.width }; }));
    for (const b of boxes) {
      expect(Math.abs(b.top - boxes[0].top)).toBeLessThanOrEqual(1); // same top / 頂端對齊
      expect(Math.abs(b.w - boxes[0].w)).toBeLessThanOrEqual(1);     // same size / 同樣大小
    }
    // 每個標籤的左右邊都要落在自己那一欄裡 / every label sits inside its own ring column
    const spill = await page.locator('.q-ring').evaluateAll((cols) => cols.flatMap((col) => {
      const c = col.getBoundingClientRect();
      return [...col.querySelectorAll('.q-label, .q-left, .q-reset, .q-pace')].map((e) => {
        const r = e.getBoundingClientRect();
        return r.left < c.left - 1 || r.right > c.right + 1 || e.scrollWidth > e.clientWidth + 1;
      });
    }));
    expect(spill.every((s) => !s)).toBe(true);
  });
}
