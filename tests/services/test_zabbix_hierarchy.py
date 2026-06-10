"""Testes unitários do parser de hierarquia Zabbix."""

from __future__ import annotations

from itgov.services.zabbix_hierarchy import _build_tree, _primary_group

# ── Dados de exemplo ─────────────────────────────────────────────────────────


def _grupos_cftv() -> list[dict]:
    """Grupos simulando estrutura CFTV/{ramo}/{dvr}."""
    return [
        {
            "name": "CFTV/Centro/DVR-1",
            "hosts": [
                {"hostid": "20", "host": "cam-01", "name": "Câmera 01"},
                {"hostid": "21", "host": "cam-02", "name": "Câmera 02"},
            ],
        },
        {
            "name": "CFTV/Norte/DVR-2",
            "hosts": [
                {"hostid": "30", "host": "cam-03", "name": "Câmera 03"},
            ],
        },
        {
            "name": "CFTV/Centro/Stand-Alone",
            "hosts": [
                {"hostid": "40", "host": "cam-standalone", "name": "Câmera Stand-Alone"},
            ],
        },
    ]


def _host_detail_completo() -> dict:
    return {
        # DVRs
        "10": {"host": "DVR-1", "name": "DVR-1 Centro", "groups": ["CFTV", "CFTV/DVRs"]},
        "11": {"host": "DVR-2", "name": "DVR-2 Norte", "groups": ["CFTV", "CFTV/DVRs"]},
        # Câmeras do DVR-1
        "20": {"host": "cam-01", "name": "Câmera 01", "groups": ["CFTV/Centro/DVR-1"]},
        "21": {"host": "cam-02", "name": "Câmera 02", "groups": ["CFTV/Centro/DVR-1"]},
        # Câmera do DVR-2
        "30": {"host": "cam-03", "name": "Câmera 03", "groups": ["CFTV/Norte/DVR-2"]},
        # Standalone
        "40": {"host": "cam-standalone", "name": "Câmera Stand-Alone", "groups": ["CFTV/Centro/Stand-Alone"]},
        # Ativo Lojas
        "50": {"host": "facial-01", "name": "Facial 01", "groups": ["Lojas/Centro/Faciais"]},
    }


# ── Testes: _primary_group ────────────────────────────────────────────────────


class TestPrimaryGroup:
    def test_retorna_grupo_mais_especifico(self) -> None:
        groups = ["CFTV", "CFTV/Centro", "CFTV/Centro/DVR-1"]
        assert _primary_group(groups) == "CFTV/Centro/DVR-1"

    def test_grupo_unico(self) -> None:
        assert _primary_group(["Lojas/Centro"]) == "Lojas/Centro"

    def test_lista_vazia(self) -> None:
        assert _primary_group([]) == ""


# ── Testes: _build_tree ───────────────────────────────────────────────────────


class TestBuildTree:
    def setup_method(self) -> None:
        self.tree = _build_tree(_grupos_cftv(), _host_detail_completo())

    def test_dvrs_identificados(self) -> None:
        dvr_hosts = {n.host for n in self.tree.dvrs.values()}
        assert "DVR-1" in dvr_hosts
        assert "DVR-2" in dvr_hosts

    def test_dvr_tem_cameras_filhas(self) -> None:
        dvr1 = next(n for n in self.tree.dvrs.values() if n.host == "DVR-1")
        assert set(dvr1.cameras) == {"20", "21"}

    def test_cameras_tem_dvr_parent(self) -> None:
        cam01 = self.tree.cameras.get("20")
        assert cam01 is not None
        assert cam01.dvr_parent == "DVR-1"
        assert cam01.ramo == "Centro"

    def test_standalone_classificado_corretamente(self) -> None:
        sa = self.tree.standalone.get("40")
        assert sa is not None
        assert sa.role == "standalone"
        assert sa.ramo == "Centro"

    def test_ativo_lojas_classificado(self) -> None:
        facial = self.tree.lojas.get("50")
        assert facial is not None
        assert facial.role == "lojas"
        assert facial.ramo == "Centro"

    def test_todos_os_hosts_em_all_hosts(self) -> None:
        ids = set(self.tree.all_hosts.keys())
        assert "10" in ids  # DVR-1
        assert "20" in ids  # cam-01
        assert "40" in ids  # standalone
        assert "50" in ids  # lojas

    def test_cameras_nao_em_lojas(self) -> None:
        for cam_id in self.tree.cameras:
            assert cam_id not in self.tree.lojas

    def test_dvr_ramo_correto(self) -> None:
        dvr1 = next(n for n in self.tree.dvrs.values() if n.host == "DVR-1")
        assert dvr1.ramo == "Centro"
        dvr2 = next(n for n in self.tree.dvrs.values() if n.host == "DVR-2")
        assert dvr2.ramo == "Norte"

    def test_grupos_vazios_nao_quebram(self) -> None:
        grupos_extras = [
            {"name": "CFTV/Vazio/DVR-99", "hosts": []},
        ]
        detail = {"99": {"host": "DVR-99", "name": "DVR-99", "groups": ["CFTV/DVRs"]}}
        tree = _build_tree(grupos_extras, detail)
        dvr99 = next((n for n in tree.dvrs.values() if n.host == "DVR-99"), None)
        assert dvr99 is not None
        assert dvr99.cameras == []

    def test_host_desconhecido_no_grupo_ignorado(self) -> None:
        grupos = [{"name": "CFTV/Centro/DVR-1", "hosts": [{"hostid": "999", "host": "fantasma"}]}]
        detail = {"10": {"host": "DVR-1", "name": "DVR-1", "groups": []}}
        tree = _build_tree(grupos, detail)
        # hostid 999 não está em host_detail → não deve aparecer
        assert "999" not in tree.cameras
