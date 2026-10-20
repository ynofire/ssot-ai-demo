// 채팅 답변 속 "다음 질문 제안"을 버튼으로 바꾸는 판정 로직(isSuggestionLike) 회귀 테스트.
// core/web/index.html 은 이 프로젝트에 번들러가 없어 단일 HTML 파일로 되어 있으므로, 함수 소스를 정규식으로 잘라내
// 격리 실행한다 (프레임워크 없이 Node 내장 기능만 사용).
//
// 사용: node tests/test_suggestion_buttons.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const html = readFileSync(join(ROOT, "core", "web", "index.html"), "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
const start = script.indexOf("function isSuggestionLike");
const end = script.indexOf("function cleanQuestionText");
if (start < 0 || end < 0) throw new Error("core/web/index.html 에서 isSuggestionLike 함수를 찾지 못했습니다 (코드가 바뀌었나요?)");
const isSuggestionLike = new Function(script.slice(start, end) + "return isSuggestionLike;")();

// [텍스트, 기대값, 설명] — 실제로 모델이 낸 답변에서 그대로 가져온 사례를 우선한다.
const CASES = [
  // 실제 리포트된 결함: "~해줘."로 끝나는 반말 요청형이 인식되지 않아, 목록 3개 중 2개가 빠지면서
  // "모든 항목이 제안이어야 버튼화" 조건을 통과하지 못해 목록 전체가 텍스트로 남았다.
  ["지난 3개월간 마진율이 가장 높거나 낮은 상품 순위는 무엇인지 알려줘.", true, "반말 요청형 (실제 결함 사례)"],
  ["지난 6개월간 매출이 감소하기 시작한 시점과 주요 원인을 알려줘.", true, "반말 요청형 (실제 결함 사례)"],
  ["올해 9월의 판매가와 사입가 추이를 분석해 줄 수 있어?", true, "물음표로 끝남"],
  ["지난 9월까지의 월별 매출, 매입, 매출총이익률, 운영비, 순현금흐름 추이는 어떤가요?", true, "물음표 + 번호 없는 문장"],
  ["지난달의 운영비 항목별 지출 내역을 항목별로 나눠서 보고 싶습니다.", true, "정중한 희망형"],
  ["운영비 항목별로 가장 큰 비중을 차지하는 항목이 무엇인가요?", true, "물음표"],
  ["작년 대비 올해 9월의 매출 변동 원인을 분석해 줄 수 있나요?", true, "물음표"],
  ["재고가 부족한 상품이 있는지 확인해 부탁해요.", true, "부탁형"],
  // 데이터 사실 나열이나 안내 서두는 버튼으로 바뀌면 안 된다 (오답이 되거나 이상한 메시지가 전송됨)
  ["대신, 현재 보유하고 있는 데이터를 바탕으로 어떤 부분을 분석해 드릴지 질문을 주시면 됩니다. 예를 들어 다음과 같은 질문을 해보시는 건 어떨까요?", false, "안내 서두(두 문장) — 물음표로 끝나지만 실제 제안이 아님"],
  ["해운대상사는 2026년 7월 3일에 이탈했으며, 이로 인해 매출이 감소했습니다.", false, "데이터 사실 나열"],
  ["매출총이익률도 25.9%에서 38.8%로 크게 개선되었습니다.", false, "데이터 사실"],
  ["현재 재고가 부족한 상품은 없습니다.", false, "데이터 사실"],
  ["PET 투명컵은 재고가 부족해 재주문이 필요합니다.", false, "데이터 사실 (예전에 뺀 '필요합니다' 어미가 다시 오탐되지 않는지)"],
  ["이 상품의 마진율은 지난달보다 하락했다.", false, "평서문"],
];

let failed = 0;
for (const [text, expected, note] of CASES) {
  const got = isSuggestionLike(text);
  const ok = got === expected;
  if (!ok) failed++;
  console.log(`${ok ? "OK  " : "FAIL"} [${note}] ${JSON.stringify(text.slice(0, 50))} -> ${got} (기대 ${expected})`);
}
console.log(`\n결과: ${CASES.length - failed}/${CASES.length} 통과`);
if (failed) process.exit(1);
