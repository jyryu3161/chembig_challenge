// npm install --no-save --package-lock=false playwright@1.58.2
// BASE_URL=http://127.0.0.1:18080 CHROMIUM_PATH=/path/to/chrome node scripts/browser-smoke.cjs
const { chromium } = require('playwright');
const fs = require('fs');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_PATH || undefined, args:['--no-sandbox']});
  const page = await browser.newPage({viewport:{width:1440,height:1050}});
  const base = process.env.BASE_URL || 'http://127.0.0.1:18080';
  const errors=[];
  page.on('pageerror', e=>errors.push(e.message));
  const response=await page.goto(base+'/');assert.equal(response.status(),200);
  assert.match(await page.title(),/ChemBIG/);
  await page.screenshot({path:'artifacts/home-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'artifacts/home-mobile.png',fullPage:true});
  await page.goto(base+'/signup/');
  const username='browser_'+Date.now();
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=real_name]').fill('브라우저 실명');
  await page.locator('[name=student_id]').fill(username);
  await page.locator('[name=nickname]').fill('실험하는 분자');
  await page.locator('[name=password1]').fill('Browser-test-password-123!');
  await page.locator('[name=password2]').fill('Browser-test-password-123!');
  await page.getByRole('button',{name:'계정 만들기'}).click();
  await page.waitForURL('**/contests/');
  await page.getByRole('link',{name:'대회 살펴보기'}).first().click();
  await page.locator('[name=invite_code]').fill('CHEMBIG-DEMO');
  await page.getByRole('button',{name:'참가 신청하기'}).click();
  assert.match(await page.locator('body').innerText(),/승인 대기/);
  fs.writeFileSync('artifacts/browser-user.json',JSON.stringify({username,cookies:await page.context().cookies()}));
  await page.screenshot({path:'artifacts/contest-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({status:'passed',username,browserErrors:errors,mobileOverflow:false}));
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
