/**
 * 日照材价信息数据抓取脚本
 * 使用 playwright 驱动 Chrome 获取动态页面数据
 */
const { chromium } = require('playwright');

const TARGET_URL = 'http://58.59.43.227:81/dist/#/index/priceDissemination';

// Chromium path from playwright install
const CHROME_PATH = '/Users/pengfit/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

/**
 * 从页面抓取表格数据
 * @param {string} tabType - 类别: '1'=建设工程材料, '2'=园林绿化苗木, '3'=区县材料
 * @param {number} maxPages - 最大页数
 * @param {number} pageSize - 每页行数
 * @returns {object} { rows, totalCount, periods, pageSize }
 */
async function fetchPriceData(tabType = '1', maxPages = 200, pageSize = 10) {
  const browser = await chromium.launch({
    executablePath: CHROME_PATH,
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
  });

  const page = await browser.newPage();
  await page.setViewportSize({ width: 1280, height: 900 });

  console.error(`[i] 打开目标页面 (type=${tabType})...`);
  await page.goto(TARGET_URL, { waitUntil: 'networkidle', timeout: 60000 });
  await sleep(3000);

  // Click the specified tab if not type 1
  if (tabType !== '1') {
    const tabs = await page.$$('.swiper-slide');
    const tabIndex = parseInt(tabType) - 1;
    if (tabs[tabIndex]) {
      await tabs[tabIndex].click();
      console.error(`[i] 已切换到类别 ${tabType}`);
      await sleep(3000);
    }
  }

  // Get current period from page
  const periods = await page.evaluate(() => {
    // Look for the period display
    const inputs = document.querySelectorAll('input');
    for (const inp of inputs) {
      if (inp.value && /\d{4}-\d{2}/.test(inp.value)) {
        return inp.value;
      }
    }
    return '';
  });

  // Get total count from pagination
  const totalCount = await page.evaluate(() => {
    const pager = document.querySelector('.el-pagination');
    if (!pager) return 0;
    const span = pager.querySelector('span.el-pagination__total');
    if (span) {
      const m = span.innerText.match(/共\s*(\d+)\s*条/);
      if (m) return parseInt(m[1]);
    }
    return 0;
  });

  console.error(`[i] 期数: ${periods}, 总记录: ${totalCount}`);

  const allRows = [];
  let currentPage = 1;
  let hasNextPage = true;

  while (currentPage <= maxPages && hasNextPage) {
    // Wait for table body to have content
    try {
      await page.waitForSelector('.el-table__body-wrapper tr', { timeout: 15000 });
    } catch (e) {
      console.error(`[!] 第 ${currentPage} 页等待表格超时`);
      break;
    }

    // Extract rows from current page
    const pageRows = await page.evaluate(() => {
      const tbody = document.querySelector('.el-table__body-wrapper');
      if (!tbody) return [];
      const rows = tbody.querySelectorAll('tr');
      return Array.from(rows).map(row => {
        const cells = row.querySelectorAll('td');
        return Array.from(cells).map(cell => {
          // Get innerText, stripping extra whitespace
          return (cell.innerText || '').replace(/\s+/g, ' ').trim();
        });
      }).filter(cells => cells.length >= 4);
    });

    if (pageRows.length === 0) {
      console.error(`[!] 第 ${currentPage} 页无数据`);
      break;
    }

    for (const cells of pageRows) {
      allRows.push({
        // 固定列: 序号, 材料名称, 规格型号, 单位, 参考价格(元), 备注
        index: cells[0] || '',
        clmc: cells[1] || '',
        ggxh: cells[2] || '',
        dw: cells[3] || '',
        price: cells[4] || '',
        remark: cells[5] || '',
      });
    }

    console.error(`[i] 第 ${currentPage} 页: 抓取 ${pageRows.length} 行 (累计 ${allRows.length})`);

    // Check if there's a next page button
    const paginationInfo = await page.evaluate(() => {
      const pager = document.querySelector('.el-pagination');
      if (!pager) return { hasNext: false, currentPage: 1 };
      const btnNext = pager.querySelector('.btn-next:not(.is-disabled), .el-pagination__next:not(.is-disabled)');
      const activePage = pager.querySelector('.el-pagination__ jumper input') ||
                         pager.querySelector('.el-pager .number.active') ||
                         pager.querySelector('.el-pager li.number.is-active');
      let currentPage = 1;
      if (activePage) {
        currentPage = parseInt(activePage.innerText) || 1;
      }
      return { hasNext: !!btnNext, currentPage };
    });

    if (!paginationInfo.hasNext || currentPage >= maxPages) {
      hasNextPage = false;
    } else {
      // Click next button
      const nextBtn = await page.$('.btn-next, .el-pagination__next');
      if (nextBtn) {
        const isDisabled = await nextBtn.evaluate(el =>
          el.classList.contains('is-disabled') || el.disabled
        );
        if (isDisabled) {
          hasNextPage = false;
        } else {
          await nextBtn.click();
          await sleep(1500);
          currentPage++;
        }
      } else {
        hasNextPage = false;
      }
    }
  }

  await browser.close();

  return {
    rows: allRows,
    totalCount,
    periods,
    pageSize,
    tabType,
  };
}

// Get metadata only (tabs, tree) without full data fetch
async function fetchMetadata() {
  const browser = await chromium.launch({
    executablePath: CHROME_PATH,
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
  });

  const page = await browser.newPage();
  await page.setViewportSize({ width: 1280, height: 900 });

  await page.goto(TARGET_URL, { waitUntil: 'networkidle', timeout: 60000 });
  await sleep(3000);

  // Get tabs
  const tabs = await page.evaluate(() => {
    const slides = document.querySelectorAll('.swiper-slide');
    return Array.from(slides).map(s => {
      const name = s.querySelector('.tab-list-item')?.innerText?.trim() || '';
      return { name };
    });
  });

  // Get current periods from date picker
  const periods = await page.evaluate(() => {
    const inputs = document.querySelectorAll('input');
    for (const inp of inputs) {
      if (inp.value && /\d{4}-\d{2}/.test(inp.value)) {
        return inp.value;
      }
    }
    return '';
  });

  await browser.close();
  return { tabs, periods };
}

async function main() {
  const args = process.argv.slice(2);
  const cmd = args[0];

  try {
    if (cmd === 'metadata') {
      const data = await fetchMetadata();
      console.log(JSON.stringify(data));
    } else {
      const tabType = args[1] || '1';
      const maxPages = parseInt(args[2]) || 200;
      const data = await fetchPriceData(tabType, maxPages);
      console.log(JSON.stringify(data));
    }
  } catch (err) {
    console.error(`[ERROR] ${err.message}`);
    process.exit(1);
  }
}

main();
