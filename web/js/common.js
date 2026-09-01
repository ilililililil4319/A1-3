// ============================================================
// 창원댁 공통 스크립트 (js/common.js)
// 모든 페이지에서 함께 쓰는 기능: 모바일 햄버거 메뉴 열고 닫기
// ============================================================

// 햄버거 버튼과 메뉴 요소를 가져온다
const navToggle = document.getElementById('navToggle');
const navMenu = document.getElementById('navMenu');

// 버튼이 있는 페이지에서만 동작하도록 확인 (안전장치)
if (navToggle && navMenu) {
  navToggle.addEventListener('click', function () {
    // 메뉴에 'open' 클래스를 켰다 껐다 → CSS가 보이기/숨기기 처리
    navMenu.classList.toggle('open');
  });
}
