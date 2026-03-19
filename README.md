# JCEd25519

JCEd25519 is a JavaCard implementation of the Ed25519 signature algorithm for cards that do not support [Named Elliptic Curves](https://blogs.oracle.com/java/post/java-card-31-cryptographic-extensions) and the [EdDSA signature algorithm](https://docs.oracle.com/en/java/javacard/3.1/jc_api_srvc/api_classic/javacard/security/Signature.html#SIG_CIPHER_EDDSA) introduced in JavaCard API 3.1.

The implementation uses a modified [JCMathLib library](https://github.com/OpenCryptoProject/JCMathLib) for elliptic-curve and big integer operations. If SHA-512 is not available on the card, a software implementation is used.

## Warning

This code is for proof-of-concept and lab testing only. It is **not** suitable for production use. The underlying arithmetic is not constant-time, so a sufficiently capable timing attacker may be able to recover the private key.

## Project Layout

- `applet/src/main/java/jced25519/JCEd25519.java`: main applet, APDU dispatch, initialization, signing flow
- `applet/src/main/java/jced25519/Consts.java`: APDU instruction values, state constants, status words
- `applet/src/main/java/jced25519/jcmathlib.java`: math library, allocator policy, resource manager, hardware helper setup
- `applet/src/main/java/jced25519/swalgs.java`: software SHA fallback
- `applet/src/test/java/tests/BaseTest.java`: simulator and physical-card connection setup
- `applet/src/test/java/tests/AppletTest.java`: end-to-end key generation and signing test
- `scripts/benchmark_allocators.py`: automated install-test-benchmark runner with HTML report output

## Prerequisites

- Clone with submodules:

```bash
git clone --recursive https://github.com/dufkan/JCEd25519
cd JCEd25519
```

- Install a Java 8 compatible JDK for Gradle and JavaCard tooling.
- Install GlobalPlatformPro as `gp`.
- Provide JavaCard SDKs under `libs-sdks/`.
- The build is currently configured for `libs-sdks/jc305u3_kit`.

## Build

The Gradle JavaCard build is configured in `applet/build.gradle`.

```bash
./gradlew applet:buildJavaCard --rerun-tasks
```

Generated CAP:

```text
applet/build/javacard/jced25519.cap
```

### Card Profile Selection

Select the target card profile in `applet/src/main/java/jced25519/JCEd25519.java` before building. The current default is:

```java
public final static short CARD = OperationSupport.JCOP4_P71;
```

Available presets in the source include:

- `OperationSupport.SIMULATOR`
- `OperationSupport.JCOP4_P71`
- `OperationSupport.JCOP3_P60`
- `OperationSupport.JCOP21`
- `OperationSupport.SECORA`

### Build Notes

- The project is now configured to use `JC305u3`. Using older kits such as `JC303` can fail during conversion with errors like `unsupported int type constant`.
- The diagnostic imports and allocator changes require a kit that exposes the required JavaCard framework classes during compilation.

## Install and Remove on a Physical Card

Package AID:

```text
6A6365643235353139
```

Applet AID:

```text
6A6365643235353139617070
```

Delete the package and its dependencies:

```bash
gp -r "ACS ACR1281 1S Dual Reader 00 01" \
  --key 404142434445464748494a4b4c4d4e4f \
  --deletedeps --delete 6A6365643235353139
```

Install the CAP with the default allocator strategy:

```bash
gp -r "ACS ACR1281 1S Dual Reader 00 01" \
  --key 404142434445464748494a4b4c4d4e4f \
  --install ./applet/build/javacard/jced25519.cap
```

Install with an explicit allocator strategy byte:

```bash
gp -r "ACS ACR1281 1S Dual Reader 00 01" \
  --key 404142434445464748494a4b4c4d4e4f \
  --install ./applet/build/javacard/jced25519.cap \
  --params 01
```

Allocator install parameter values:

- `00`: RAM allocator, fastest but highest transient RAM pressure
- `01`: tradeoff allocator, mixed RAM and persistent allocation, safer on constrained physical cards
- `02`: EEPROM allocator, lowest RAM usage and usually the slowest

The applet now parses the first install-data byte and uses it as the allocator strategy. If no install data is provided, it defaults to `tradeoff`.

## APDU Behavior

The applet no longer requires a dedicated initialization APDU from the host test flow.

- `SELECT` only selects the applet.
- The first business APDU triggers lazy initialization inside `process()`.
- If the applet is still uninitialized when the first command arrives, `initialize()` runs before instruction dispatch.

This means the old README sequence that suggested sending `00DF000000` is no longer valid for the current codebase.

Common instruction values:

- `00 D0 01 00 00`: key generation with offloaded public-key conversion
- `00 D3 00 00 <32B>`: set externally converted public key
- `00 D4 01 00 00`: sign initialization with offloaded nonce conversion
- `00 D5 00 00 <32B>`: provide converted public nonce
- `00 D7 00 00 00`: sign update
- `00 D6 <len> 00 <msg>`: sign finalize
- `00 D8 00 00 00`: debug-only private nonce readout

## Testing

The main physical-card regression test is:

```bash
./gradlew applet:test --tests tests.AppletTest.keygen_and_sign -Djc.test.readerIndex=1 --rerun-tasks
```

Reader index selection is now configurable:

- JVM property: `-Djc.test.readerIndex=1`
- Environment variable fallback: `JC_TEST_READER_INDEX=1`

Measurement output is also configurable:

```bash
./gradlew applet:test \
  --tests tests.AppletTest.keygen_and_sign \
  -Djc.test.readerIndex=1 \
  -Djc.test.measurementFile=applet/measurement.csv \
  --rerun-tasks
```

`AppletTest` records these columns:

- `sign_init`
- `sign_nonce`
- `nonce`
- `sign_update`
- `sign_finalize`

The test performs:

1. Connect to the configured reader.
2. Select the applet.
3. Run key generation in offload mode.
4. Run 10 signing rounds.
5. Verify each Ed25519 signature on the host side.

### Known Good Physical Test Flow

For the reader `ACS ACR1281 1S Dual Reader 00 01`, the following flow was confirmed to work:

```bash
./gradlew applet:test --tests tests.AppletTest.keygen_and_sign -Djc.test.readerIndex=1 --rerun-tasks
```

If the same command fails immediately after `SELECT`, inspect memory pressure on the card before changing code.

## Benchmarking Allocator Strategies

Use the automation script to reinstall the applet with multiple allocator strategies, run the existing JUnit test, collect timings, and generate an HTML report:

```bash
python3 scripts/benchmark_allocators.py \
  --reader "ACS ACR1281 1S Dual Reader 00 01" \
  --key 404142434445464748494a4b4c4d4e4f \
  --reader-index 1
```

Default comparison set:

- `ram`
- `tradeoff`

Example including all strategies and multiple repeats:

```bash
python3 scripts/benchmark_allocators.py \
  --reader "ACS ACR1281 1S Dual Reader 00 01" \
  --key 404142434445464748494a4b4c4d4e4f \
  --reader-index 1 \
  --strategies ram tradeoff eeprom \
  --repeats 5
```

Generated artifacts are written under:

```text
benchmark-results/<timestamp>/
```

Including:

- `summary.csv`
- `summary.json`
- `report.html`
- per-strategy install/delete/test logs
- per-run measurement CSV files

The script is written to remain compatible with older Python 3 runtimes commonly found in lab containers.

## Status Words and Debugging

### Applet-Level Status Words

- `9000`: success
- `6F00`: generic failure returned by the platform or an uncaught error path
- `EE00`: already initialized
- `EE01`: applet not initialized
- `EE02`: debug-only instruction used while `DEBUG == false`
- `EE03`: invalid applet state

### Exception Prefixes

The applet maps Java and JavaCard exceptions to status word prefixes:

- `FF01`: generic `Exception`
- `FF02`: `ArrayIndexOutOfBoundsException`
- `FF03`: `ArithmeticException`
- `FF04`: `ArrayStoreException`
- `FF05`: `NullPointerException`
- `FF06`: `NegativeArraySizeException`
- `F1xx`: `CryptoException`
- `F2xx`: `SystemException`
- `F3xx`: `PINException`
- `F4xx`: `TransactionException`
- `F5xx`: `CardRuntimeException`

### Initialization Diagnostics

Initialization now emits stage-specific status words:

- `E1xx`: `CryptoException` during applet initialization
- `E2xx`: `SystemException` during applet initialization
- `E3xx`: `CardRuntimeException` during applet initialization
- `E4xx`: other exception during applet initialization
- `ECxx`: `CryptoException` during `ResourceManager` setup
- `EDxx`: `SystemException` during `ResourceManager` setup
- `EExx`: `CardRuntimeException` during `ResourceManager` setup

The low byte is the stage number. Example:

- `ED04`: `SystemException` at `ResourceManager` stage 4
- In this project, that points to BigNat helper allocation during resource setup

### Practical Failure Analysis

Observed failure sequence on a physical card:

```text
SELECT AID           -> 9000
00 D0 01 00 00      -> ED04
```

Root cause:

- not a reader-selection issue
- not an AID-selection issue
- transient card memory pressure during applet helper allocation
- confirmed by removing another installed applet and rerunning the same test successfully

Recommended debug steps:

1. Confirm the correct reader is selected with `-Djc.test.readerIndex=<n>`.
2. List installed content with `gp -l`.
3. Remove unused packages or applets from the card.
4. Reinstall this applet with allocator parameter `01` or `02`.
5. Rerun `tests.AppletTest.keygen_and_sign`.
6. If it still fails, capture the exact status word and map it using the tables above.

## Details

The implementation requires random nonce generation for security. This is a minor deviation from the canonical Ed25519 derivation flow, but it is not externally observable unless the same message is signed repeatedly under conditions that allow nonce misuse.

## Supported Cards

The implementation was tested on NXP J3R200, NXP J3H145, NXP J2E145G, and Infineon Secora ID S.
