async function uiPresentationHostRegression(page, baseURL) {
  const context=await page.context().browser().newContext({viewport:{width:1336,height:900}}),p=await context.newPage(),requests=[];
  p.on('request',r=>requests.push(r.url()));
  const text='FAST_FRAME_FIRST_7391',revision='fixture-source-v1';
  try {
    await p.route('**/open?*',async route=>{const u=new URL(route.request().url());if(u.searchParams.get('renderer')==='pdf')return route.continue();
      const base={name:'Synthetic.pptx',path:'synthetic/Synthetic.pptx',revision:'saved-B-v1'};
      await route.fulfill({json:u.searchParams.get('mode')==='result'?{...base,doc:{extracted:true,html:'<h1>Slide MD</h1><p data-block-id="b-old">'+text+'</p>',md_text:'# Slide MD\n\n'+text},source_ready:false,pages:[]}:
        {...base,source_kind:'presentation',source_ready:true,pages:[[960,720],[960,720],[960,720]],source_revision:revision,source_data_url:'/presentation-data?p=1&r='+revision,source_frame_url:'/presentation-frame',renderer_version:'1.4.0',source_unit_label:'슬라이드',source_label:'원본 슬라이드'}});});
    await p.route('**/document-map?*',async route=>{const u=new URL(route.request().url());
      await route.fulfill({json:{revision:'saved-B-v1',source_kind:'presentation',source_revision:revision,coordinate_space:'presentation_intrinsic_pixels',pages:[{width:960,height:720}],blocks:[{id:'b-old',text,slide:0,locations:[],logical_source:{slide:0,shape:2,paragraph:0}}]}});});
    await p.goto(baseURL);await p.waitForSelector('.file-row');
    await p.locator('.file-row[data-index="1"] [data-action=read]').click();await p.waitForSelector('#document [data-block-id]');
    await p.locator('#search').fill('FIRST');await p.locator('#search-form').evaluate(n=>n.requestSubmit());
    await p.waitForFunction(()=>window.__calls.some(c=>c.name==='search'));
    const resultOnlySearch=await p.evaluate(()=>window.__calls.find(c=>c.name==='search'));
    if(resultOnlySearch.args[3]!=='')throw new Error('UI_PRESENTATION_RESULT_SEARCH_FORCES_PDF_RED '+JSON.stringify(resultOnlySearch));
    await p.locator('#reader-back').click();await p.locator('.file-row[data-index="1"]').dblclick();
    try{await p.waitForSelector('#presentation-frame',{timeout:2500});}catch{throw new Error('UI_PRESENTATION_HOST_RED no fast frame');}
    const f=()=>p.frames().find(f=>f.url().includes('/presentation-frame#'));
    await p.waitForFunction(()=>document.querySelector('#presentation-frame')?.dataset.ready==='true');
    const frame=f(),first=await frame.locator('.slide-slot').first().boundingBox();
    if(!(await frame.locator('body').innerText()).includes(text))throw new Error('UI_PRESENTATION_HOST_CONTENT_RED');
    await frame.evaluate(()=>{window.__wheel=[];document.addEventListener('wheel',e=>window.__wheel.push({trusted:e.isTrusted,prevented:e.defaultPrevented}),{passive:true});});
    await p.mouse.move(first.x+first.width/2,first.y+first.height/2);await p.keyboard.down('Control');try{await p.mouse.wheel(0,-100);}finally{await p.keyboard.up('Control');}
    await p.waitForFunction(()=>document.querySelector('#source-zoom-reset').textContent==='110%');
    await frame.waitForFunction(()=>document.querySelector('.slide-slot')?.getBoundingClientRect().width>1000);
    const zoomed={width:(await frame.locator('.slide-slot').first().boundingBox()).width,wheel:await frame.evaluate(()=>window.__wheel),md:await p.locator('#md-zoom-reset').textContent()};
    if(Math.abs(zoomed.width/first.width-1.1)>.03||zoomed.md!=='100%'||!zoomed.wheel.some(e=>e.trusted&&e.prevented))throw new Error('UI_PRESENTATION_HOST_ZOOM_RED '+JSON.stringify(zoomed));
    await p.locator('#search').fill('THIRD');await p.locator('#search-form').evaluate(n=>n.requestSubmit());
    await p.waitForFunction(()=>document.querySelector('#page-input').value==='3');
    await frame.waitForFunction(()=>document.body.innerText.includes('FAST_FRAME_THIRD_7391'));
    await frame.waitForFunction(()=>document.querySelectorAll('[data-pptx-search-highlight]').length===1);
    const searchSlide=await frame.locator('[data-pptx-search-highlight]').evaluate(n=>n.closest('.slide-slot')?.dataset.slideIndex);if(searchSlide!=='2')throw new Error('UI_PRESENTATION_HOST_SEARCH_TARGET_RED '+searchSlide);
    const searchDisplay=await p.locator('#location-note').textContent();
    if(searchDisplay)throw new Error('UI_PRESENTATION_SEARCH_RESIZE_ERROR_RED '+searchDisplay);
    const nativeSearches=await p.evaluate(()=>window.__calls.filter(c=>c.name==='search_source'));
    if(nativeSearches.length)throw new Error('UI_PRESENTATION_HOST_NATIVE_SEARCH_RED');
    await p.locator('#compare-tab').click();await p.waitForSelector('#document [data-block-id]');
    await p.locator('#content-tab').click();await p.locator('#document [data-block-id]').click();
    await p.waitForFunction(()=>document.querySelector('#page-input').value==='1');
    if(!await p.locator('#reader').evaluate(n=>n.classList.contains('compare')))throw new Error('UI_PRESENTATION_LINK_STAYS_HIDDEN_RED');
    await frame.waitForFunction(()=>document.querySelector('[data-pptx-search-highlight]'));
    const logical=await frame.evaluate(()=>({highlight:!!document.querySelector('[data-pptx-search-highlight]'),slide:document.querySelector('[data-pptx-search-highlight]')?.closest('.slide-slot')?.dataset.slideIndex,text:document.body.innerText}));
    if(!logical.text.includes(text)||logical.slide!=='0')throw new Error('UI_PRESENTATION_HOST_LOGICAL_LINK_RED');
    const wrong=await p.evaluate(()=>{const frame=document.querySelector('#presentation-frame');window.dispatchEvent(new MessageEvent('message',{data:{channel:'pubnexus.presentation.v1',type:'rendered',nonce:'bad',revision:'bad',page:99},source:frame.contentWindow}));window.dispatchEvent(new MessageEvent('message',{data:{channel:'pubnexus.presentation.v1',type:'rendered',nonce:new URL(frame.src).hash.slice(1),revision:'fixture-source-v1',page:3},source:window}));return document.querySelector('#page-input').value;});
    if(wrong!=='1')throw new Error('UI_PRESENTATION_HOST_FORGERY_RED');
    await p.locator('#theme-toggle').click();await p.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
    await frame.waitForFunction(()=>document.body.dataset.theme==='dark');
    const dark=await frame.evaluate(()=>({paper:getComputedStyle(document.querySelector('.slide-slot')).backgroundColor,filters:[...document.images].map(i=>getComputedStyle(i).filter)}));
    if(dark.paper!=='rgb(255, 255, 255)'||dark.filters.some(f=>f!=='none'))throw new Error('UI_PRESENTATION_HOST_COLOR_RED '+JSON.stringify(dark));
    await p.setViewportSize({width:375,height:850});
    await p.waitForTimeout(120);
    const mobile=await p.evaluate(()=>({width:document.documentElement.scrollWidth,viewport:innerWidth,frame:document.querySelector('#presentation-frame').getBoundingClientRect().width}));
    if(mobile.width>mobile.viewport||mobile.frame>mobile.viewport)throw new Error('UI_PRESENTATION_HOST_OVERFLOW_RED '+JSON.stringify(mobile));
    await p.locator('#reader-back').click();if(await p.locator('#presentation-frame').count())throw new Error('UI_PRESENTATION_HOST_DISPOSE_RED');
    const pageCalls=requests.filter(u=>new URL(u).pathname==='/page');
    if(pageCalls.length)throw new Error('UI_PRESENTATION_HOST_PDF_MIX_RED '+JSON.stringify(pageCalls));
    return {ok:true,resultOnlySearch,first,zoomed,searchDisplay,logical,wrong,dark,mobile,nativeSearches,pageCalls};
  } finally {await context.close();}
}
