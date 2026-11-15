import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {CONTROL,SPACE} from '../../src/bmpt/web/js/ui-tokens.js';
import {row} from '../../src/bmpt/web/js/ui-grid.js';
const S=1920/2960;
const source=readFileSync(new URL('../../src/bmpt/web/js/photography/controller.js',import.meta.url),'utf8');
const slice=(from,to)=>source.slice(source.indexOf(from),source.indexOf(to));
// scrollbar.js renders prefabs at import; its pure track geometry is taken from the source
const bar=readFileSync(new URL('../../src/bmpt/web/js/scrollbar.js',import.meta.url),'utf8');
const scrollbarTrack=new Function(`${bar.slice(bar.indexOf('export function scrollbarTrack(')+7,bar.indexOf('export async function scrollbar('))};return scrollbarTrack;`)();
const code=slice('function galleryPair(','// The captions of the two text buttons')+slice('async function openGallery(','async function deletePhoto()')+slice('async function restoreGame()','async function applyGame()');

function harness(count,{selected='p0',scroll=null}={}){
  const photos=Array.from({length:count},(_,index)=>({id:`p${index}`,name:`Photo ${index}`,url:`p${index}.png`}));
  const areas=[],buttons=[];
  const env={CONTROL,SPACE,row,S,P:key=>key,safe:fn=>fn,DEFAULT:'generated',OFFICIAL:'official',saving:false,
    bridge:{pause:()=>{},thumbnailChanged:()=>{}},repositoryRead:async()=>{},galleryPhotos:()=>photos,photo:id=>photos.find(item=>item.id===id),
    Popup:{selectionReady:async()=>{},listCard:(item,_x,_y,selected)=>({...item,selected})},NGUI:{render:async()=>{}},
    Loading:{task:fn=>fn()},waitJob:async()=>{},call:async()=>({}),generatedPNG:async()=>'png',untestedRetry:async()=>false,
    image:async()=>{},applyGame:async()=>{},deletePhoto:async()=>{},
    UI:{buttonSize:()=>({}),button:async(_layer,name)=>buttons.push(name),caption:async()=>{},
      createModal:()=>({draw:fn=>fn({},()=>true,null),close:()=>{}}),
      // the real area keeps the offset in positions; every row renders here, each into its own fake cell
      virtualArea:(_host,box,rows,pitch,render,options)=>{
        const cells=Array.from({length:rows},()=>({cards:[],hits:[]}));areas.push({box,rows,pitch,options,cells});
        return {ready:Promise.all(cells.map((cell,index)=>render(cell,index,null)))};
      }},
  };
  env.NGUI.render=async(tree,cell,{origin})=>cell.cards.push({key:tree.key,selected:tree.selected,origin});
  const hitButton=(cell,box,action,label)=>cell.hits.push({box,action,label});
  const api=new Function('env','hitButton',`const {${Object.keys(env)}}=env;let selected=${JSON.stringify(selected)},modal=UI.createModal(),generation=1,repository={photos:[],app:null,game:{}};
    const GALLERY_SCROLL='photo-gallery',galleryScroll=new Map(${scroll?`[['photo-gallery',${JSON.stringify(scroll)}]]`:''});
    ${code};return {galleryListLayout,galleryReveal,openGallery,restoreGame,galleryDraw,offset:()=>galleryScroll.get(GALLERY_SCROLL)?.[1],
      scroll:value=>galleryScroll.set(GALLERY_SCROLL,[0,value]),select:id=>{selected=id;}};`)(env,hitButton);
  return {...api,areas,buttons};
}

test('the gallery list sits between the first row of cards and the tool row, its scrollbar clear of cards and preview',()=>{
  const {galleryListLayout}=harness(0),{box:[left,top,width,height],pitch,columns,column,card}=galleryListLayout();
  assert.equal(pitch,236);assert.equal(columns,3);assert.equal(column,215);
  assert.equal(left,365);
  assert.ok(top+height<=835-SPACE.l*S+1e-9,'the box ends SPACE.l above the tool row');
  const track=scrollbarTrack([left,top,width,height],S);
  assert.ok(Math.max(left+width,track[0]+track[2])<=1190-SPACE.xl*S,'neither the box nor its track reaches the preview');
  for(let col=0;col<columns;col++){
    const x=card[0]+col*column,y=card[1],hit=[x-92,y-80,184,210];
    assert.equal(left+x,465+col*215,'the card columns stay where they were on screen');
    assert.ok(hit[0]>=0&&hit[0]+hit[2]<=width,'the card lies inside the box horizontally');
    assert.ok(hit[1]>0&&hit[1]+hit[3]<pitch,'the card lies inside its row pitch, a margin for the selected frame');
    assert.ok(left+hit[0]+hit[2]<=track[0],'the scrollbar track covers no card');
  }
  assert.equal(top+card[1],343,'the first row stays where it was');
  assert.ok(height>2*pitch,'the list shows more than two rows');
});

for(const count of [7,13,40])test(`${count} photos draw as one list of rows of three, without Previous / Next`,async()=>{
  const h=harness(count,{selected:'p1'});await h.galleryDraw();
  assert.equal(h.areas.length,1);
  const [{rows,pitch,cells,options}]=h.areas,expected=Math.ceil(count/3);
  assert.equal(rows,expected);assert.equal(pitch,236);assert.equal(options.key,'photo-gallery');
  assert.equal(cells.at(-1).cards.length,count-3*(expected-1));
  assert.deepEqual(cells.flatMap(cell=>cell.cards.map(card=>card.key)),Array.from({length:count},(_,index)=>`p${index}`));
  assert.deepEqual(cells.flatMap(cell=>cell.cards.filter(card=>card.selected).map(card=>card.key)),['p1']);
  assert.deepEqual(cells.flatMap(cell=>cell.hits.map(hit=>hit.label)),Array.from({length:count},(_,index)=>`Photo ${index}`));
  assert.ok(!h.buttons.includes('Previous')&&!h.buttons.includes('Next'));
  assert.ok(['Restore','Export','SaveSelection','Apply','Delete'].every(name=>h.buttons.includes(name)));
  assert.ok(!/[\s;,(]page[=;,)+]/.test(source)&&!source.includes("P('previous')"),'no page state or paging buttons are left');
});

test('opening shows the selected row, selecting a card keeps the offset, Restore goes back to the top',async()=>{
  const h=harness(40,{selected:'p0',scroll:[0,500]}),{box,pitch}=h.galleryListLayout();
  await h.openGallery(true);assert.equal(h.offset(),0,'a photo in the first rows opens at the top');
  for(const index of [5,8,20,39]){
    h.select(`p${index}`);await h.openGallery(true);
    const offset=h.offset(),rowTop=Math.floor(index/3)*pitch;
    assert.ok(rowTop>=offset&&rowTop+pitch<=offset+box[3]+1e-9,`row of p${index} is visible at ${offset}`);
    assert.ok(Math.abs(Math.max(0,rowTop+pitch-box[3])-offset)<1e-9,'scrolled just enough from the top');
  }
  h.scroll(1234);
  const area=h.areas.at(-1);await area.cells[2].hits[1].action();
  assert.equal(h.offset(),1234,'selecting a card keeps the offset');
  assert.equal(h.areas.at(-1).options.positions,area.options.positions,'the redraw reads the same offsets');
  assert.ok(h.areas.at(-1).cells[2].cards[1].selected);
  await h.restoreGame();assert.equal(h.offset(),0);
});

test('galleryReveal moves the offset as little as possible',()=>{
  const {galleryReveal,galleryListLayout}=harness(0),{box,pitch}=galleryListLayout();
  assert.equal(galleryReveal(-1),0);assert.equal(galleryReveal(2),0);
  assert.equal(galleryReveal(3,1000),pitch,'a row above the view scrolls up to its top');
  assert.equal(galleryReveal(9,0),4*pitch-box[3],'a row below the view scrolls down to its bottom');
  assert.equal(galleryReveal(9,3*pitch),3*pitch,'a visible row keeps the offset');
});
