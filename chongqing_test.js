const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    ignoreHTTPSErrors: true,
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });
  const page = await ctx.newPage();
  
  page.on('console', msg => {
    if (msg.type() === 'error') console.log('CONSOLE ERROR:', msg.text());
  });
  page.on('response', res => {
    const url = res.url();
    if (!url.includes('.js') && !url.includes('.css') && !url.includes('.png')) {
      console.log('RESPONSE:', res.status(), url.substring(0, 120));
    }
  });

  try {
    // First visit homepage to get cookies
    await page.goto('http://www.cqsgczjxx.org', { timeout: 15000 });
    await page.waitForTimeout(2000);
    
    console.log('Cookies:', await ctx.cookies());
    
    // Then try the target page
    await page.goto('http://www.cqsgczjxx.org/Pages/CQZJW/priceInformation.aspx', {
      waitUntil: 'networkidle',
      timeout: 30000
    });

    const title = await page.title();
    console.log('Title:', title);
    
    const html = await page.content();
    console.log('HTML length:', html.length);

    const bodyText = await page.locator('body').innerText().catch(() => '');
    console.log('Body text (first 500):', bodyText.substring(0, 500));

  } catch (e) {
    console.error('Error:', e.message);
  } finally {
    await browser.close();
  }
})();