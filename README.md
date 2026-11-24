# XPS Sample Studio

Conversor **e criador de instrumentos** com interface gráfica que transforma instrumentos do **Kontakt (.nki)**, **SoundFont (.sf2)**,
**SFZ** e pastas de **WAV** em amostras prontas para o **Roland XPS-10X** (e também para o XPS-10 antigo).
Funciona no **macOS** (Apple Silicon e Intel) e no **Windows**.

## O que ele faz

### Criador de instrumentos (novo na 1.1)
Monte seus próprios multisamples a partir de WAVs e exporte de forma funcional para **Roland XPS-10X (.svz)**,
**Kontakt (.nki)**, **SoundFont (.sf2)** e **SFZ**.

- **Amostras:** arraste WAV/AIFF/NCW ou pastas inteiras. A nota de cada amostra vem do nome do arquivo
  (`Piano_C4`, `Bass_Fs2`, `Str_Eb3`, `Kick_60`) ou de **detecção automática de afinação**.
- **Mapa tecla × velocidade** (como o Mapping Editor do Kontakt): arraste zonas para mover, arraste bordas para mudar a
  faixa, Shift para mover na velocidade, setas para ajuste fino, zoom com Ctrl+roda, clique no teclado para ouvir.
- **Camadas (grupos):** várias camadas com nome, volume, pan, afinação, mudo/solo e **envelope ADSR** editável
  graficamente. “Dividir vel.” reparte 1–127 entre as camadas (camadas de velocidade).
- **Editor de amostra:** forma de onda com zoom e marcadores arrastáveis de início, fim e loop; normalizar, inverter,
  fade-in/out, remover silêncio, detectar nota, **encontrar loop automaticamente** e crossfade da emenda.
- **Desfazer/refazer**, salvar/abrir projeto (`.xpsinst`) e “Editar no Criador” para abrir qualquer NKI/SF2/SFZ
  convertido.
- **Exportação:** SVZ com até 4 camadas de velocidade (mono) ou 2 (estéreo); NKI no formato Kontakt 1 (abre em todas
  as versões do Kontakt) com pasta de WAVs; SF2 com amostras embutidas, faixas, loops e envelopes; SFZ + WAVs.

### Conversor

| Etapa | Recursos |
|---|---|
| **1. Importar** | Arraste e solte `.nki`, `.sf2`, `.sfz`, WAV/AIFF ou uma pasta de WAVs. Em SoundFonts com vários presets, você escolhe o preset numa lista com busca. |
| **2. Mapa de teclas** | Reduz o número de amostras (de 1 até 8, o limite de partes de uma Scene do XPS-10X). As notas retiradas são cobertas pelas amostras vizinhas. Você escolhe a camada de velocidade, a extensão do teclado, a distribuição (centralizada, para cima ou para baixo) e pode **fixar** amostras que não devem sair. Clicar numa tecla toca o som exatamente como vai ficar no teclado. |
| **3. Som e EQ** | Equalizador de 5 bandas com curva interativa (corta-graves, graves, médios, agudos, corta-agudos) e predefinições. Normalização (individual ou em grupo), remoção de silêncio inicial, duração máxima, fade-out, crossfade de loop, conversão para mono. Comparação A/B entre original e processado. |
| **4. Exportar (SVZ)** | Gera um **timbre .SVZ** pronto: todas as amostras (48 kHz) já mapeadas no teclado num multisample, importado de uma vez por **MENU › UTILITY › IMPORT TONE**. Mono usa 1 partial; estéreo usa 2 partials (esquerdo/direito). Até 64 amostras por timbre. |
| **4. Exportar (WAVs)** | Gera a pasta `IMPORT` com WAVs 44,1 kHz (16 ou 24 bits), nomes válidos para o teclado, mais um **guia de importação** (HTML e TXT) com Original Key, faixa de teclas de cada parte e pontos de loop. Um medidor mostra quanto dos ~12 minutos de memória de amostras será usado. |

### Regras do XPS-10X que o programa respeita
- WAV 44,1/48 kHz, 16/24 bits, na pasta `IMPORT` de um pendrive formatado no próprio teclado.
- Nomes com até 24 caracteres (com a extensão), só letras e números.
- Cerca de 3 minutos por amostra e cerca de 12 minutos no total (memória dividida com as Expansions).
- Cada amostra soa da tecla mais grave até 3 oitavas acima da Original Key. O programa nunca deixa uma parte passar disso.
- Uma Scene tem 8 partes, cada uma com KEY RANGE próprio. Por isso o multisample fica com no máximo 8 amostras.
- O XPS-10X não tem afinação fina por amostra. A afinação fina do instrumento original é aplicada direto no áudio.
- O loop repete do ponto de loop até o fim, então o áudio é cortado logo após o fim do loop.

## Formatos de entrada e limites (leia!)

- **SoundFont .sf2**: suporte completo (zonas, faixas de tecla/velocidade, raiz, afinação, loops e pares estéreo).
- **SFZ**: regiões, grupos, `default_path`, loops, afinação e volume.
- **Kontakt .nki** (leitura direta, sem precisar do Kontakt):
  - **Kontakt 1 a 4.1** (XML), **Kontakt 4.2** e **Kontakt 5, 6, 7 e 8** (contêiner da Native Instruments).
  - **Monólitos** (o `.nki` com as amostras dentro do próprio arquivo).
  - Amostras **WAV, AIFF e NCW** (formato comprimido da Native Instruments), decodificadas automaticamente.
  - Deixe o `.nki` junto da pasta `Samples` da biblioteca (do jeito que ela vem instalada). O programa procura
    as amostras na pasta do `.nki` e na pasta acima dela.
  - Zonas de *release* (som ao soltar a tecla) e grupos mudos são ignorados.
  - As amostras só são lidas do disco quando são usadas, então bibliotecas grandes abrem rápido.
  - **Bibliotecas protegidas/criptografadas** (Kontakt Player, `.nkx` comerciais) **não podem** ser convertidas.
    O programa avisa quando encontra uma.

> A leitura dos formatos do Kontakt 4.2+ e NCW é uma tradução para Python do trabalho de engenharia reversa do
> [ConvertWithMoss](https://github.com/git-moss/ConvertWithMoss) (Jürgen Moßgraber). Esses dois módulos
> (`xps_studio/core/kontakt5.py` e `ncw.py`) seguem a licença LGPLv3.

## Como usar no teclado

### Timbre .SVZ (recomendado)
1. Exporte no formato “Timbre .SVZ”. É criada a pasta `ROLAND/SOUND/<NOME>.SVZ`.
2. Copie a pasta `ROLAND` para a raiz do pendrive formatado no XPS-10X.
3. No teclado: **MENU › UTILITY › IMPORT TONE**, escolha o arquivo e confirme.
4. Se o teclado recusar o arquivo, exporte qualquer timbre do próprio XPS-10X (**UTILITY › EXPORT TONE**)
   e, no programa, use **“Usar SVZ exportado do meu teclado…”**. O programa copia a identificação de modelo
   desse arquivo para os próximos SVZ.

> O formato SVZ é o mesmo dos teclados Roland ZEN-Core (FANTOM, JUNO-X, JUPITER-X, MC-707).
> O gravador (`xps_studio/core/svz.py`) segue a engenharia reversa do ConvertWithMoss (LGPLv3) e usa
> registros-modelo gravados por um teclado Roland real.

### WAVs + guia (Sample Import)
1. Exporte. Vai ser criada uma pasta com `IMPORT/` e `GUIA_XPS10X_<nome>.html`.
2. Formate o pendrive no XPS-10X e copie os WAVs para a pasta `IMPORT` dele.
3. No teclado: **MENU › SAMPLE › IMPORT** para cada arquivo. Cada importação cria um User Tone.
4. Ajuste Original Key e loop no EDIT SAMPLE conforme o guia, coloque cada User Tone numa parte e ajuste o
   **KEY RANGE** (SHIFT + MENU › KEY TOUCH › aba KEY RANGE). Depois grave a Scene.

Os WAVs também levam a raiz e o loop no bloco `smpl` do arquivo. Se o teclado ler esses dados, você economiza ajustes.
Se não ler, use os valores do guia.

## Rodar a partir do código
```bash
pip install -r requirements.txt
python run.py
```

## Gerar os aplicativos
- **macOS**: `./build_mac.sh` (ou dois cliques em `Criar App Mac.command`) → `dist/XPS-Sample-Studio-macOS.dmg`
- **Windows**: `build_windows.bat` → `dist\XPS Sample Studio\XPS Sample Studio.exe` (+ .zip)
- **Automático (GitHub)**: envie o projeto para um repositório e crie uma tag `v1.0.0`. O workflow em
  `.github/workflows/build.yml` compila Mac Apple Silicon, Mac Intel e Windows e publica os instaladores na
  página de Releases.

> No macOS, como o app não é assinado com certificado da Apple, na primeira vez abra com
> **clique direito › Abrir**.

## Testes
```bash
python -m pytest tests
```

## Estrutura
```
xps_studio/core/   leitores (sf2, nki, sfz, wav), DSP (audio.py), redução e exportação (engine.py)
xps_studio/ui/     interface PySide6 (tema, teclado, forma de onda, curva de EQ)
packaging/         configuração do PyInstaller e ícones
```

## Licença
Os módulos `xps_studio/core/kontakt5.py`, `ncw.py`, `svz.py` e `exporters.py` (gravação de NKI) e os modelos em
`xps_studio/assets/svz/` e `xps_studio/assets/nki/` derivam do
[ConvertWithMoss](https://github.com/git-moss/ConvertWithMoss) e seguem a **LGPL-3.0** (veja `LICENSE-LGPL-3.0.txt`).

A fonte Inter (`xps_studio/assets/fonts`) é distribuída sob a SIL Open Font License (veja `LICENSE-Inter-OFL.txt`).
