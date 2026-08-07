"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.CliError = void 0;
/**
 * Thrown to signal a well-classified CLI failure. Carries the exit code
 * and the exact shape of the JSON document that must be printed to stdout.
 */
class CliError extends Error {
    exitCode;
    errorCode;
    index;
    sku;
    constructor(exitCode, errorCode, message, extra) {
        super(message);
        this.exitCode = exitCode;
        this.errorCode = errorCode;
        this.index = extra?.index;
        this.sku = extra?.sku;
    }
    toOutput() {
        const out = {
            ok: false,
            error_code: this.errorCode,
            message: this.message,
        };
        if (this.index !== undefined) {
            out.index = this.index;
        }
        if (this.sku !== undefined) {
            out.sku = this.sku;
        }
        return out;
    }
}
exports.CliError = CliError;
