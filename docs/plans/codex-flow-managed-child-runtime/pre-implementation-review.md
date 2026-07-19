# Pre-Implementation Review

## 구현 판단

`진행 가능`.

원인은 env pair 누락 자체가 아니라 canonical runtime이 required closure preparation을 소유하지 않는 데 있으며, 계획은 이 책임을 `runner.execute_unit`의 기존 pre-edit boundary에 둔다. 기존 exact manifest, fresh app-server inventory, no-edit oracle을 제거하지 않고 precondition만 안전하게 충족한다. 아래 important findings는 계획 본문에 반영됐고 blocker는 남지 않았다.

## 상세 검토 결과

### Blocker

발견된 문제 없음.

### Important — 반영 완료

1. **Broad parent inventory를 external allowlist로 쓰면 exact closure가 과도하게 넓어질 위험**
   - 근거: 기존 Chronica provisioner는 discovered non-plugin id 전체를 external로 기록한다. 현재 plan 초안도 parent/child inventory 차이를 전제로 했다.
   - failure: unrelated personal skill 또는 global plugin이 무해한 ambient 항목처럼 승인될 수 있다.
   - 반영: parent inventory sweep을 제거했다. staged child의 provisional `skills/list` path를 `skills/`, `plugins/cache/.../<version>`, child 밖 external로 분류한다. child plugin payload는 manifest에서 tree hash로 묶고 ownership mapping이 모호하면 deny한다.

2. **Attested home과 actual implementation/review home이 갈라질 위험**
   - 근거: `run_codex_exec()`는 독립적으로 `child_runtime_environment()`를 호출하고 현재 구현은 global env를 다시 읽는다.
   - failure: attestation은 A home을 검증했지만 implementer 또는 resumed review가 B home을 실행할 수 있다.
   - 반영: `PreparedChildRuntime` 결과를 generate/verify/exec/agent에 explicit parameter로 전달한다. env는 direct caller compatibility fallback으로만 남긴다. implementation과 same-session review 양쪽의 metadata/home equality assertion을 계획과 테스트에 추가했다.

3. **Repo-only cache namespace는 closure source drift와 concurrent replacement를 구분하지 못함**
   - 근거: 현재 `child_runtime_environment()` reuse test는 동일 repo → 동일 directory만 증명한다.
   - failure: 다른 required closure가 active runtime을 교체하거나 stale skill source가 재사용될 수 있다.
   - 반영: repo + normalized required/resolved ids + source tree digest + plugin/external inventory + sanitized config + CLI/schema version을 content key로 사용한다. per-key thread/file lock, unique staging, atomic rename, invalid existing closure deny를 성공 기준으로 고정했다.

4. **Plugin skill을 external id로만 처리하면 path digest 계약이 약해짐**
   - 근거: current v1 manifest는 plugin payload tree hash를 지원하며 `_inventory_skill_ids()`도 plugin path ownership을 검증한다.
   - failure: child cache 안에 실제 payload가 있는데 id만 allowlist하고 content를 bind하지 않을 수 있다.
   - 반영: provisional inventory path가 child plugin cache에 속하면 payload root와 skill ids를 group해 manifest `plugins` entry로 기록한다. path root ambiguity, multiple-owner overlap, undeclared version payload를 negative test로 둔다.

5. **Preparation failure observability가 generic exception 한 줄에 머물 위험**
   - 근거: runner는 현재 `needs_work before edit: {exc}`만 기록한다.
   - failure: source resolution, staged discovery, cache validation, publish race 중 어디서 막혔는지 재현 없이 구분하기 어렵다.
   - 반영: `managed_child_runtime_prepare|reuse|denied` event와 stage, repo/closure fingerprint, required ids, manifest digest를 기록한다. auth/token/config 원문과 payload 내용은 금지한다.

### Minor — 반영 완료

- content-addressed home 전체를 immutable하다고 표현하면 Codex 자체 sqlite/log/session 쓰기와 충돌한다. immutable 범위를 attested closure payload로 제한하고 runtime-owned volatile state는 허용한다고 명시했다.
- HTML artifact는 이 CLI/backend 작업의 판단 표면이 아니므로 생략 사유와 pytest/raw CLI 대체 검토물을 계획에 명시했다.

## 코드 계약 검토

```python
@dataclass(frozen=True)
class PreparedChildRuntime:
    home: Path
    manifest: Path
    source: str      # managed | explicit
    cache_key: str
    reused: bool
```

- manager는 `PreparedChildRuntime`만 반환하고 process env를 변경하지 않는다.
- `generate_child_attestation(..., child_home, manifest_path)`와 `verify_child_attestation(..., child_home, manifest_path)`는 동일 객체의 path를 사용한다.
- `CodexImplementerAgent(..., child_home)`는 implementation과 resumed review 모두 같은 home을 `run_codex_exec()`에 넘긴다.
- explicit env는 both-or-none 계약이며 manager가 override directory를 수정/교체하지 않는다.
- half-pair, unsafe source/path, ambiguous suffix, staged/fresh inventory drift, invalid cache는 `ChildRuntimeConfigError` 또는 `ChildAttestationError`로 runner의 기존 no-edit catch에 합류한다.

## 회귀 위험과 테스트 공백 검토

- no-env success: fake app-server/exec를 사용해 network/model 비용 없이 managed prepare → attest → implementer launch를 증명.
- no-edit negatives: missing source, internal symlink, path escape, half-pair, manifest tamper, plugin owner ambiguity, fresh inventory drift마다 launch marker 없음 + HEAD/index/worktree oracle 동일.
- cache: identical reuse, source/config/CLI/inventory invalidation, corrupt existing same-key denial.
- concurrency: same-key two callers가 validated same winner를 받고 staging/partial path를 반환하지 않음.
- compatibility: explicit pair, repo-local override rejection, parent-home override rejection, explicit profile/custom-provider no-managed-copy rule.
- regression: focused runner suites + full pytest + CLI help + diff check.

## 선제적 진단 로그 계획

| 예상 failure mode | 삽입 경계/분기 | event·severity | 필수 field | 정상/이상 기준 | 민감정보·비용 제어 | 검증·제거 조건 |
| --- | --- | --- | --- | --- | --- | --- |
| source resolution denied | manager resolver | `managed_child_runtime_denied` / warning | `stage=resolve`, repo fingerprint, required ids, error class | exact unique source / missing·ambiguous·symlink | absolute source content, token, config 원문 금지 | negative tests assert event + no edit; permanent |
| staged inventory ownership mismatch | provisional discovery | same / warning | `stage=inventory`, closure fingerprint, unmatched ids | every id has one owner / zero·multiple owner | full payload/path 대신 normalized id와 path class만 | inventory negative test; permanent |
| cache reuse/publish race | lock/publish | `managed_child_runtime_prepare|reuse|denied` / info·warning | cache key prefix, reused, manifest digest | one validated winner / corrupt or publish failure | sampling unnecessary: unit당 최대 1회 | concurrency test; info event may be lowered after stable release |
| attested home differs from exec home | agent launch | `managed_child_runtime_denied` / error | attested home fingerprint, exec home fingerprint, unit id | equal / mismatch | absolute auth/config/token 금지 | same-home test; permanent invariant |

## 다음 task

1. 본 계획을 `$해결전략검토`의 adversarial proof gate로 검토한다.
2. 판정이 `적절`이고 blocker가 없으면 Commit 1 → 2 → 3을 `구현커밋`의 `run-next`로 순차 실행한다.
3. self-preparation 호출부가 연결되는 Commit 2까지만 기존 explicit prepared pair를 bootstrap에 사용할 수 있고, Commit 3과 최종 smoke는 env pair 없는 raw command로 증명한다.

## Review Gate

`REVIEW_GATE status="pass" blockers=0 important=0 minor=0 reason="managed preparation ownership, exact path classification, explicit runtime threading, cache atomicity, no-edit tests, and diagnostic contracts are plan-ready"`

독립 evaluator `managed-child-plan-evaluator-20260719`는 제한 시간 내 결과를 반환하지 못해 중단했다. 동일 concern을 재위임하지 않았고, 메인 에이전트가 실제 plan/source/test 근거로 review-all-in-one + review-swarm 판정을 완료했다.
