// Offline contract tests. Does not connect to or change a real cart.
const assert = require('node:assert/strict');
const AliBom = require('../scripts/browser.js');
let clicks = 0, count = 1, enabled = true, failAfterClick = false;
const tab = {
  url: async () => 'https://aliexpress.ru/item/1000000000000.html',
  playwright: {
    getByRole: (role, opts) => {
      assert.equal(role, 'button'); assert.equal(opts.exact, true);
      return {count: async () => count, isEnabled: async () => enabled,
        click: async () => {clicks++; if (failAfterClick) throw Error('timeout');}};
    },
    domSnapshot: async () => 'Synthetic post-click state'
  }
};
const request = (id) => ({operation_id:id, operation_status:'new', authorized:true,
  variant_verified:true, quantity_verified:true, price_verified:true,
  cart_baseline_verified:true, product_id:'1000000000000', sku_key:'test', quantity:2, button_name:'В корзину'});
(async () => {
  assert.throws(() => AliBom.productUrl('https://aliexpress.ru.evil.test/item/1.html'));
  assert.equal(AliBom.productUrl('https://aliexpress.ru/item/123.html?tracking=secret').url, 'https://aliexpress.ru/item/123.html');
  assert.equal(AliBom.productUrl('https://aliexpress.ru/item/123.html?sku_id=456&tracking=secret').url, 'https://aliexpress.ru/item/123.html?sku_id=456');
  assert.throws(() => AliBom.productUrl('https://aliexpress.ru/item/123.html?sku_id=bad'));
  assert.throws(() => AliBom.productUrl('https://aliexpress.ru/item/123.html?sku_id=456&sku_id=789'));
  for (const change of [{authorized:false}, {quantity:0}, {variant_verified:false},
    {cart_baseline_verified:false}, {button_name:'Купить сейчас'}, {product_id:'999'}, {operation_status:'attempting'}]) {
    await assert.rejects(AliBom.addOne(tab, {...request('rejected'),...change}));
  }
  assert.equal(clicks, 0);
  count = 2; await assert.rejects(AliBom.addOne(tab, request('ambiguous'))); count = 1;
  enabled = false; await assert.rejects(AliBom.addOne(tab, request('disabled'))); enabled = true;
  const added = await AliBom.addOne(tab, request('success'));
  assert.equal(added.status, 'verification_required'); assert.equal(clicks, 1);
  await assert.rejects(AliBom.addOne(tab, request('success'))); assert.equal(clicks, 1);
  failAfterClick = true;
  await assert.rejects(AliBom.addOne(tab, request('uncertain')));
  assert.equal(clicks, 2);
  await assert.rejects(AliBom.addOne(tab, request('uncertain'))); assert.equal(clicks, 2);
  const readTab = text => ({url: async () => 'https://aliexpress.ru/wholesale?SearchText=test&session=secret',
    playwright: {evaluate: async () => ({text,links:[{url:'https://aliexpress.ru/item/123.html?tracking=secret',text:'Recommendation',context:'Recommendation'}],images:[],resources:[]})}});
  const emptySearch = await AliBom.observe(readTab('Ничего не найдено'));
  assert.equal(emptySearch.search_status, 'empty');
  assert.equal(emptySearch.url.includes('secret'), false);
  assert.equal(emptySearch.candidates[0].url, 'https://aliexpress.ru/item/123.html');
  assert.equal((await AliBom.observe(readTab('Доступ ограничен'))).search_status, 'blocked');
  const unloaded = readTab('Loading'); unloaded.playwright.evaluate = async () => ({text:'Loading',links:[],images:[],resources:[]});
  assert.equal((await AliBom.observe(unloaded)).search_status, 'not_ready');
  let selectedUrl='https://aliexpress.ru/item/123.html?sku_id=456', navigations=[];
  const productTab={url:async()=>selectedUrl,goto:async u=>{selectedUrl=u;navigations.push(u)},
    playwright:{domSnapshot:async()=>'Product and observed option labels',
      evaluate:async()=>({text:'Synthetic product',links:[],images:[],resources:[]})}};
  const preserved=await AliBom.product(productTab,'https://aliexpress.ru/item/123.html');
  assert.equal(preserved.url_sku_id,'456'); assert.deepEqual(navigations,[]);
  const changed=await AliBom.product(productTab,'https://aliexpress.ru/item/123.html?sku_id=789&tracking=secret');
  assert.deepEqual(navigations,['https://aliexpress.ru/item/123.html?sku_id=789']);
  assert.equal(changed.url_sku_id,'789'); assert.equal(changed.variant_verified,false);
  let optionClicks=0, selectedLabel;
  const optionLocator={count:async()=>1,isVisible:async()=>true,isEnabled:async()=>true,innerText:async()=>'320W',
    click:async()=>{optionClicks++},selectOption:async ({label})=>{selectedLabel=label}};
  const optionResult=await AliBom.chooseVariantOption(productTab,{product_id:'123',optionLocator});
  assert.equal(optionClicks,1); assert.equal(optionResult.status,'selection_verification_required');
  await AliBom.chooseVariantOption(productTab,{product_id:'123',optionLocator,nativeLabel:'3500K'});
  assert.equal(selectedLabel,'3500K');
  await assert.rejects(AliBom.chooseVariantOption(productTab,{product_id:'999',optionLocator}));
  await assert.rejects(AliBom.chooseVariantOption(productTab,{product_id:'123',optionLocator:{...optionLocator,isEnabled:async()=>false}}));
  await assert.rejects(AliBom.chooseVariantOption(productTab,{product_id:'123',optionLocator:{...optionLocator,innerText:async()=>'В корзину'}}));
  process.stdout.write('Browser contract tests passed (offline, no real cart changed).\n');
})().catch(e => {console.error(e); process.exitCode = 1;});
