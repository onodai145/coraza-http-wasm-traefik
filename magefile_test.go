//go:build mage

package main

import (
	"archive/zip"
	"os"
	"path/filepath"
	"testing"
)

func TestUnzipRejectsUnexpectedPath(t *testing.T) {
	dir := t.TempDir()
	archive := filepath.Join(dir, "artifact.zip")
	f, err := os.Create(archive)
	if err != nil {
		t.Fatal(err)
	}
	w := zip.NewWriter(f)
	entry, _ := w.Create("../escape.wasm")
	entry.Write([]byte("bad"))
	w.Close()
	f.Close()
	if err := unzip(archive); err == nil {
		t.Fatal("expected invalid archive rejection")
	}
}

func TestVersionMustBeExplicit(t *testing.T) {
	t.Setenv("VERSION", "")
	if _, err := getHttpWasmVersion(); err == nil {
		t.Fatal("VERSION must be required")
	}
}

func TestChecksumFailurePreservesInstalledWasm(t *testing.T) {
	dir := t.TempDir()
	dst := filepath.Join(dir, "coraza-http-wasm.wasm")
	os.WriteFile(dst, []byte("old"), 0644)
	t.Setenv("WASM_SHA256", "wrong")
	if err := installWasm(dir, []byte("new"), "test"); err == nil {
		t.Fatal("expected checksum mismatch")
	}
	data, _ := os.ReadFile(dst)
	if string(data) != "old" {
		t.Fatal("installed artifact changed on validation failure")
	}
}
