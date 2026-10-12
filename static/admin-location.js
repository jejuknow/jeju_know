/* Shared address lookup with an optional click/drag position picker. */
(function () {
  'use strict';
  const button=document.getElementById('addressLocate');
  if(!button)return;
  const message=document.getElementById('locationFeedback');
  const latitude=document.querySelector('[name="latitude"]'),longitude=document.querySelector('[name="longitude"]');
  const address=document.querySelector('[name="address"]');
  const canvas=document.getElementById('placeLocationMap'),details=document.getElementById('locationPicker');
  let maps,map,marker,picker,revision=0;
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
    const current=++revision;showPosition(position);
    message.textContent='지도 위치를 선택했습니다. 주소를 확인하고 저장해주세요.';
    new maps.services.Geocoder().coord2Address(position.getLng(),position.getLat(),(results,status)=>{
      if(current!==revision)return;
      if(status===maps.services.Status.OK&&results.length){
        const found=results[0].road_address||results[0].address;
        if(found?.address_name){address.value=found.address_name;message.textContent=found.address_name+' · 지도 위치와 주소가 입력되었습니다. 건물·상세 주소를 확인해주세요.';}
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
  button.addEventListener('click',async()=>{
    const value=address.value.trim();if(!value){message.textContent='주소를 먼저 입력해 주세요.';return;}
    const current=++revision;button.disabled=true;message.textContent='주소 위치를 확인하고 있어요.';
    try{
      const sdk=await window.JejunoKakao.load(button.dataset.kakaoKey);
      const results=await new Promise((resolve,reject)=>{
        new sdk.services.Geocoder().addressSearch(value,(data,status)=>{
          if(status===sdk.services.Status.OK&&data.length)resolve(data);
          else reject(new Error('정확한 도로명·지번 주소로 다시 검색하거나 지도에서 위치를 선택해주세요.'));
        });
      });
      if(current!==revision)return;
      if(results.length!==1)throw new Error('주소가 여러 곳에 해당합니다. 건물 번호까지 입력해 주세요.');
      await ensurePicker();if(current!==revision)return;
      const position=new sdk.LatLng(Number(results[0].y),Number(results[0].x));
      showPosition(position);if(map){map.setCenter(position);map.setLevel(4);}
      message.textContent=results[0].address_name+' 위치를 찾았습니다. 주소와 좌표를 확인하고 저장해주세요.';
    }catch(error){if(current===revision)message.textContent=error.message;}
    finally{button.disabled=false;}
  });
  document.getElementById('locationClear')?.addEventListener('click',()=>{
    revision++;latitude.value='';longitude.value='';marker?.setMap(null);message.textContent='지도 위치를 비웠습니다. 주소는 유지됩니다.';
  });
  [latitude,longitude].forEach(input=>input.addEventListener('change',()=>{
    revision++;const value=coordinates();
    if(map&&value){const position=new maps.LatLng(value.lat,value.lng);showPosition(position);map.setCenter(position);}
    else marker?.setMap(null);
  }));
})();
