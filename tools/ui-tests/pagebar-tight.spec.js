const { test, expect } = require('@playwright/test');

// 頁面很多的橫向選單列比螢幕寬時，只有目前頁面保留名稱（.pb-tight）；寬螢幕與側邊欄不受影響。
// A bottom page bar wider than the screen keeps a name only on the current page; wide screens and the side bar are untouched.
const PAGES = Array.from({ length: 11 }, (_, i) => ({ id: 'p' + i, title: 'Page ' + (i + 1), icon: '★', widgets: [] }));

async function load(page, size, display = {}) {
  await page.setViewportSize(size);
  await page.goto('/panel/style.css');
  await page.evaluate(([pages, display]) => {
    localStorage.clear();
    localStorage.setItem('pb-demo-config', JSON.stringify({ night: { enabled: false }, shortcuts: [], pages,
      display: { idleReturnSec: 0, pageBar: 'bar', ...display } }));
  }, [PAGES, display]);
  await page.goto('/panel/');
  await expect(page.locator('#pager .page')).toHaveCount(PAGES.length);
  await expect(page.locator('#dots')).toHaveClass(/pagebar/); // the bar is on (pageBar: 'bar')
}

const visibleNames = (page) => page.locator('#dots .dot .dot-nm:visible').count();

test('a narrow screen with many pages shows only the current name', async ({ page }) => {
  await load(page, { width: 750, height: 360 });
  await expect(page.locator('#dots')).toHaveClass(/pb-tight/);
  expect(await visibleNames(page)).toBe(1);
});

test('a wide screen keeps every name', async ({ page }) => {
  await load(page, { width: 1600, height: 900 });
  await expect(page.locator('#dots')).not.toHaveClass(/pb-tight/);
  expect(await visibleNames(page)).toBe(PAGES.length);
});

test('the side bar is never tightened', async ({ page }) => {
  await load(page, { width: 750, height: 360 }, { pageBarSide: 'left' });
  await expect(page.locator('#dots')).toHaveClass(/pb-vertical/);
  await expect(page.locator('#dots')).not.toHaveClass(/pb-tight/);
  expect(await visibleNames(page)).toBe(PAGES.length);
});
