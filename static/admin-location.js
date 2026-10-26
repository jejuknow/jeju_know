/* Shared address lookup with an optional click/drag position picker. */
(function () {
  'use strict';
  const button=document.getElementById('addressLocate');
  if(!button)return;
  const message=document.getElementById('locationFeedback');
  const latitude=document.querySelector('[name="latitude"]'),longitude=document.querySelector('[name="longitude"]');
  const address=document.querySelector('[name="address"]');
  const canvas=document.getElementById('placeLocationMap'),details=document.getElementById('locationPicker');
  const automatic=details?.hasAttribute('data-auto-address'),form=address.form;
  const normalize=value=>value.trim().replace(/\s+/g,' ');
  let maps,map,marker,picker,revision=0,timer,pending=Promise.resolve();
  let knownAddress=normalize(address.value),searchedAddress=knownAddress;
  function coordinates(){
    if(!latitude.value||!longitude.value)return null;
    const lat=Number(latitude.value),lng=Number(longitude.value);
    return Number.isFinite(lat)&&Number.isFinite(lng)&&Math.abs(lat)<=90&&Math.abs(lng)<=180?{lat,lng}:null;
  }
  function showPosition(position){
    latitude.value=position.getLat().toFixed(7);longitude.value=position.getLng().toFixed(7);
    if(marker){marker.setPosition(position);marker.setMap(map);}
  }
  function choose(position){
    clearTimeout(timer);searchedAddress=knownAddress=normalize(address.value);
    const current=++revision;button.disabled=false;showPosition(position);
    message.textContent='지도 위치를 수정했습니다. 이 위치로 저장됩니다.';
    if(automatic&&address.value.trim())return;
    new maps.services.Geocoder().coord2Address(position.getLng(),position.getLat(),(results,status)=>{
      if(current!==revision)return;
      if(status===maps.services.Status.OK&&results.length){
        const found=results[0].road_address||results[0].address;
        if(found?.address_name){address.value=found.address_name;searchedAddress=knownAddress=normalize(address.value);message.textContent=found.address_name+' · 지도 위치와 주소가 입력되었습니다. 건물·상세 주소를 확인해주세요.';}
      }
    });
  }
  async function ensurePicker(){
    if(!canvas)return;
    if(!picker)picker=window.JejunoKakao.load(button.dataset.kakaoKey).then(sdk=>{
      maps=sdk;const saved=coordinates();const center=new maps.LatLng(saved?.lat||33.38,saved?.lng||126.55);
      map=new maps.Map(canvas,{center,level:saved?4:10});
      marker=new maps.Marker({position:center,draggable:true});if(saved)marker.setMap(map);
      maps.event.addListener(map,'click',event=>choose(event.latLng));
      maps.event.addListener(marker,'dragend',()=>choose(marker.getPosition()));
      new ResizeObserver(()=>{const center=map.getCenter();map.relayout();map.setCenter(center);}).observe(canvas);
    });
    await picker;const center=map.getCenter();map.relayout();map.setCenter(center);
  }
  details?.addEventListener('toggle',()=>{if(details.open)ensurePicker().catch(error=>{message.textContent=error.message;});});
  function search(){
    clearTimeout(timer);
    const value=address.value.trim();if(!value){message.textContent='주소를 먼저 입력해 주세요.';return;}
    const current=++revision;button.disabled=true;message.textContent='주소 위치를 확인하고 있어요.';
    searchedAddress=knownAddress=normalize(value);
    pending=(async()=>{try{
      const sdk=await window.JejunoKakao.load(button.dataset.kakaoKey);
      const result=await window.JejunoKakao.geocode(button.dataset.kakaoKey,value);
      if(current!==revision)return;
      if(!automatic||details?.open)await ensurePicker();if(current!==revision)return;
      const position=new sdk.LatLng(result.lat,result.lng);
      showPosition(position);if(map){map.setCenter(position);map.setLevel(4);}
      message.textContent='위치 확인됨 · 지도 위치를 찾았습니다.';
    }catch(error){if(current===revision){message.textContent=error.message;if(automatic&&details)details.open=true;}}
    finally{if(current===revision)button.disabled=false;}})();
    return pending;
  }
  button.addEventListener('click',search);
  if(automatic){
    const changed=()=>{
      const value=normalize(address.value);if(value===knownAddress)return;
      knownAddress=value;searchedAddress=null;revision++;clearTimeout(timer);button.disabled=false;
      latitude.value=longitude.value='';marker?.setMap(null);
      message.textContent=value?'주소가 변경되어 지도 위치를 다시 확인합니다.':'주소를 입력하면 지도 위치를 자동으로 찾습니다.';
      if(value)timer=setTimeout(search,650);
    };
    address.addEventListener('input',changed);
    address.addEventListener('blur',()=>{changed();if(normalize(address.value)!==searchedAddress)search();});
    form.prepareLocation=async()=>{
      changed();clearTimeout(timer);
      if(address.value.trim()&&normalize(address.value)!==searchedAddress)await search();
      else await pending;
    };
    if(knownAddress&&!coordinates()){searchedAddress=null;timer=setTimeout(search,650);}
  }
  document.getElementById('locationClear')?.addEventListener('click',()=>{
    revision++;clearTimeout(timer);searchedAddress=knownAddress=normalize(address.value);button.disabled=false;
    latitude.value='';longitude.value='';marker?.setMap(null);message.textContent='지도 위치를 비웠습니다. 주소는 유지됩니다.';
  });
  [latitude,longitude].forEach(input=>input.addEventListener('change',()=>{
    revision++;button.disabled=false;const value=coordinates();
    if(map&&value){const position=new maps.LatLng(value.lat,value.lng);showPosition(position);map.setCenter(position);}
    else marker?.setMap(null);
  }));
})();
