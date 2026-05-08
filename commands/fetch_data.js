/**
 * 日照材价信息数据抓取脚本
 * 使用 playwright 驱动 Chrome 获取动态页面数据
 *
 * 三种模式:
 *   node fetch_data.js metadata                  - 获取 tabs + periods
 *   node fetch_data.js paginate <type> <page>   - 单页 JSON（兼容旧调用）
 *   node fetch_data.js stream <type> <maxPages>  - 流式输出，每抓完一页立即输出一行 JSON Lines
 */
const { chromium } = require('playwright');

const TARGET_URL = 'http://58.59.43.227:81/dist/#/index/priceDissemination';
const CHROME_PATH = '/Users/pengfit/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

/**
 * 等待表格行出现，使用更精确的条件
 */
async function waitTable(page, timeout = 20000) {
  try {
    await page.waitForSelector('.el-table__body-wrapper tbody tr', { timeout });
    // 等一小段时间让 Vue 渲染完成
    await sleep(300);
    return true;
  } catch (e) {
    return false;
  }
}

/**
 * 从当前页面提取 rows
 */
async function extractRows(page) {
  return page.evaluate(() => {
    const tbody = document.querySelector('.el-table__body-wrapper tbody');
    if (!tbody) return [];
    return Array.from(tbody.querySelectorAll('tr')).map(row => {
      const cells = row.querySelectorAll('td');
      return Array.from(cells).map(c => (c.innerText || '').replace(/\s+/g, ' ').trim());
    }).filter(cells => cells.length >= 5);
  });
}

/**
 * 初始化浏览器并访问目标页，返回 browser + page + periods
 */
async function initBrowser(tabType) {
  const browser = await chromium.launch({
    executablePath: CHROME_PATH,
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
  });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1280, height: 900 });

  await page.goto(TARGET_URL, { waitUntil: 'networkidle', timeout: 60000 });
  await sleep(2500);

  // 切换 tab
  if (tabType !== '1') {
    const tabs = await page.$$('.swiper-slide');
    const idx = parseInt(tabType) - 1;
    if (tabs[idx]) {
      await tabs[idx].click();
      // 等待 Vue 切换完成后网络空闲
      await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
      await sleep(1000);
    }
  }

  // 读取当前期数
  const periods = await page.evaluate(() => {
    for (const inp of document.querySelectorAll('input')) {
      if (inp.value && /\d{4}-\d{2}/.test(inp.value)) return inp.value;
    }
    return '';
  });

  return { browser, page, periods };
}

/**
 * 获取总记录数
 */
async function getTotalCount(page) {
  return page.evaluate(() => {
    const span = document.querySelector('.el-pagination__total');
    if (span) {
      const m = span.innerText.match(/共\s*(\d+)\s*条/);
      if (m) return parseInt(m[1]);
    }
    return 0;
  });
}

/**
 * 点击"下一页"按钮，成功返回 true
 */
async function clickNext(page) {
  const isDisabled = await page.evaluate(() => {
    const btn = document.querySelector('.btn-next');
    return !btn || btn.classList.contains('is-disabled') || btn.disabled;
  });
  if (isDisabled) return false;

  const nextBtn = await page.$('.btn-next');
  await nextBtn.click();
  // 等待网络响应而不是固定 sleep
  await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
  await sleep(500);
  return true;
}

/**
 * 流式模式：每抓完一页立即 flush 输出，109 页连续抓取
 */
async function streamPages(tabType, maxPages) {
  const { browser, page, periods } = await initBrowser(tabType);
  const totalCount = await getTotalCount(page);
  const pageSize = 10;
  const totalPages = Math.ceil(totalCount / pageSize) || 1;
  const maxToFetch = Math.min(totalPages, maxPages);

  console.error(`[i] 期数: ${periods}, 总记录: ${totalCount}, 总页数: ${totalPages}`);

  // 第1页
  const rows0 = await extractRows(page);
  const pageRows = rows0.map(cells => ({
    index: cells[0], clmc: cells[1], ggxh: cells[2], dw: cells[3], price: cells[4], remark: cells[5]
  }));
  console.log(JSON.stringify({ page: 1, rows: pageRows, totalCount, totalPages, periods, pageSize }));
  console.error(`[i] 第 1 页: ${pageRows.length} 行`);

  // 第2页及以后：连续翻页，每次等待网络空闲
  let currentPage = 1;
  while (currentPage < maxToFetch) {
    const ok = await clickNext(page);
    if (!ok) {
      console.error(`[i] 已到最后一页 (${currentPage})`);
      break;
    }

    // 等待表格刷新（行数变化 或 小睡）
    const rows = await extractRows(page);
    currentPage++;

    const pageRowsN = rows.map(cells => ({
      index: cells[0], clmc: cells[1], ggxh: cells[2], dw: cells[3], price: cells[4], remark: cells[5]
    }));
    console.log(JSON.stringify({ page: currentPage, rows: pageRowsN, totalCount, totalPages, periods, pageSize }));
    console.error(`[i] 第 ${currentPage} 页: ${pageRowsN.length} 行`);
  }

  console.log(JSON.stringify({ done: true, totalCount, totalPages, periods }));
  await browser.close();
}

/**
 * 单页模式（兼容旧调用）
 */
async function fetchOnePage(tabType, targetPage) {
  const { browser, page, periods } = await initBrowser(tabType);
  const totalCount = await getTotalCount(page);
  const pageSize = 10;
  const totalPages = Math.ceil(totalCount / pageSize) || 1;

  // 翻到目标页（目标页 > 1 时用页码跳转）
  if (targetPage > 1) {
    // 尝试页码输入框直接跳转
    const jumpInput = await page.$('.el-pagination__jump input, .el-pagination__jump .el-input__inner');
    if (jumpInput) {
      await jumpInput.fill(String(targetPage));
      await jumpInput.press('Enter');
      await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
      await sleep(800);
    } else {
      // 逐页翻（最坏情况）
      let cur = 1;
      while (cur < targetPage) {
        const ok = await clickNext(page);
        if (!ok) break;
        cur++;
      }
    }
  }

  const rows = await extractRows(page);
  const pageRows = rows.map(cells => ({
    index: cells[0], clmc: cells[1], ggxh: cells[2], dw: cells[3], price: cells[4], remark: cells[5]
  }));

  console.log(JSON.stringify({ page: targetPage, rows: pageRows, totalCount, totalPages, periods, pageSize }));
  if (targetPage < totalPages) {
    console.log(JSON.stringify({ done: true, totalCount, totalPages, periods }));
  }
  await browser.close();
}

/**
 * 元数据模式
 */
async function fetchMetadata() {
  const { browser, page, periods } = await initBrowser('1');
  const tabs = await page.evaluate(() =>
    Array.from(document.querySelectorAll('.swiper-slide'))
      .map(s => ({ name: s.querySelector('.tab-list-item')?.innerText?.trim() || '' }))
  );
  await browser.close();
  return { tabs, periods };
}

async function main() {
  const [cmd, arg1, arg2] = process.argv.slice(2);
  try {
    if (cmd === 'metadata') {
      const data = await fetchMetadata();
      console.log(JSON.stringify(data));
    } else if (cmd === 'stream') {
      await streamPages(arg1 || '1', parseInt(arg2) || 200);
    } else {
      // 默认单页模式（兼容旧调用 paginate）
      await fetchOnePage(arg1 || '1', parseInt(arg2) || 1);
    }
  } catch (err) {
    console.error(`[ERROR] ${err.message}`);
    process.exit(1);
  }
}

main();