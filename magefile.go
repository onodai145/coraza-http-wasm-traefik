//go:build mage

package main

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path"
	"regexp"
	"strings"

	_ "embed"

	"github.com/magefile/mage/sh"
)

func download(url, dst string) error {
	fmt.Printf("Downloading %s to %s\n", url, dst)
	// Create the file
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()

	// Get the data
	resp, err := http.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("bad status: %s", resp.Status)
	}

	// Write the body to file
	_, err = io.Copy(out, resp.Body)
	if err != nil {
		return err
	}

	return nil
}

func unzip(name string) error {
	reader, err := zip.OpenReader(name)
	if err != nil {
		return err
	}
	defer reader.Close()
	if len(reader.File) != 1 || reader.File[0].Name != "coraza-http-wasm.wasm" || reader.File[0].UncompressedSize64 > 32<<20 {
		return errors.New("archive must contain only coraza-http-wasm.wasm")
	}
	in, err := reader.File[0].Open()
	if err != nil {
		return err
	}
	defer in.Close()
	data, err := io.ReadAll(io.LimitReader(in, 32<<20))
	if err != nil {
		return err
	}
	if len(data) < 8 || string(data[:8]) != "\x00asm\x01\x00\x00\x00" {
		return errors.New("invalid WASM artifact")
	}
	return installWasm(path.Dir(name), data, "archive:"+name)
}

const artifactURL = "https://github.com/onodai145/coraza-http-wasm/releases/download/{version}/coraza-http-wasm-{version}.zip"

func downloadHTTPWasmArtifact(version, dir string) error {
	url := strings.Replace(artifactURL, "{version}", version, 2)
	if err := download(url, path.Join(dir, "coraza-http-wasm.zip")); err != nil {
		return err
	}

	if err := unzip(path.Join(dir, "coraza-http-wasm.zip")); err != nil {
		return err
	}

	return os.Remove(path.Join(dir, "coraza-http-wasm.zip"))
}

func getHttpWasmVersion() (string, error) {
	version := os.Getenv("VERSION")
	if !regexp.MustCompile(`^v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$`).MatchString(version) {
		return "", errors.New("set VERSION to an explicit release tag, e.g. v0.4.0-onodai.1")
	}
	return version, nil
}

func DownloadArtifact() error {
	if os.Getenv("WASM_SHA256") == "" {
		return errors.New("WASM_SHA256 is required for release downloads")
	}
	var err error
	if err = os.Mkdir("build", 0755); err != nil && !os.IsExist(err) {
		return err
	}

	version, err := getHttpWasmVersion()
	if err != nil {
		return err
	}

	if err := downloadHTTPWasmArtifact(version, "./build"); err != nil {
		return err
	}
	return writeProvenance("release:" + version)
}

func copy(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()

	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()

	_, err = io.Copy(out, in)
	return err
}

// PrepareLocal installs a reviewed local build, or an explicitly selected release.
func PrepareLocal() error {
	if err := os.MkdirAll("build", 0755); err != nil {
		return err
	}
	source := os.Getenv("WASM_SOURCE")
	if source == "" {
		if err := DownloadArtifact(); err != nil {
			return err
		}
	} else {
		data, err := os.ReadFile(source)
		if err != nil {
			return err
		}
		if len(data) < 8 || string(data[:8]) != "\x00asm\x01\x00\x00\x00" {
			return errors.New("invalid WASM source")
		}
		if err := installWasm("build", data, source); err != nil {
			return err
		}
	}
	return copy(".traefik.yml", "build/.traefik.yml")
}

func writeProvenance(source string) error {
	data, err := os.ReadFile("build/coraza-http-wasm.wasm")
	if err != nil {
		return err
	}
	return installWasm("build", data, source)
}

func installWasm(dir string, data []byte, source string) error {
	sum := sha256.Sum256(data)
	digest := hex.EncodeToString(sum[:])
	if expected := os.Getenv("WASM_SHA256"); expected != "" && !strings.EqualFold(expected, digest) {
		return fmt.Errorf("WASM SHA256 mismatch: got %s", digest)
	}
	tmp, err := os.CreateTemp(dir, ".wasm-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tmp.Name(), 0644); err != nil {
		return err
	}
	if err := os.Rename(tmp.Name(), path.Join(dir, "coraza-http-wasm.wasm")); err != nil {
		return err
	}
	if err := os.WriteFile(path.Join(dir, "SHA256SUMS"), []byte(digest+"  coraza-http-wasm.wasm\n"), 0644); err != nil {
		return err
	}
	return os.WriteFile(path.Join(dir, "SOURCE"), []byte(source+"\n"), 0644)
}

type e2eSource string

const (
	e2eSourceLocal  e2eSource = "local"
	e2eSourceRemote e2eSource = "remote"
)

func e2e(source e2eSource) error {
	var err error

	dockerComposeArgs := []string{"compose", "-f", "docker-compose.yml", "-f", "e2e/docker-compose.e2e.yml"}

	if err = sh.RunV("docker", append(dockerComposeArgs, "up", "-d", "e2e_traefik_"+string(source))...); err != nil {
		return err
	}
	defer func() {
		_ = sh.RunV("docker", append(dockerComposeArgs, "down", "-v")...)
	}()

	proxyHost := os.Getenv("TRAEFIK_HOST")
	if proxyHost == "" {
		proxyHost = "localhost:18080"
	}
	httpbinHost := os.Getenv("HTTPBIN_HOST")
	if httpbinHost == "" {
		httpbinHost = "localhost:18000"
	}

	if err = sh.RunV("go", "run", "github.com/corazawaf/coraza/v3/http/e2e/cmd/httpe2e@v3.2.1", "--proxy-hostport",
		"http://"+proxyHost, "--httpbin-hostport", "http://"+httpbinHost); err != nil {
		sh.RunV("docker", append(dockerComposeArgs, "logs", "e2e_traefik_"+string(source))...)
	}

	return err
}

func E2E() error {
	return E2ELocal()
}

func E2ELocal() error {
	if err := PrepareLocal(); err != nil {
		return err
	}
	return e2e(e2eSourceLocal)
}

//go:embed config-static.yaml.tmpl
var staticConfig string

func UpdateVersion() error {
	if os.Getenv("VERSION") == "" {
		return errors.New("VERSION environment variable is not set")
	}

	renderedStaticConfig := strings.Replace(staticConfig, "{{version}}", os.Getenv("VERSION"), 1)

	return errors.Join(
		os.WriteFile("e2e/config-static.remote.yaml", []byte(renderedStaticConfig), 0644),
	)
}
