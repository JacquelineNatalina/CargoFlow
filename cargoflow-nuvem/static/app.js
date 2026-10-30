// Atualiza a página sozinha a cada 30 segundos, para que a tela Operação
// e a tela Carregamento mostrem o que a outra equipe acabou de registrar.
// Não atualiza se alguém estiver digitando ou com um formulário alterado.
(function () {
    const INTERVALO_MS = 30000;

    // Marca o formulário como "alterado" assim que o usuário mexe em algum campo
    document.addEventListener("input", function (evento) {
        const formulario = evento.target.closest("form");
        if (formulario) {
            formulario.dataset.sujo = "true";
        }
    });

    function podeAtualizar() {
        if (document.hidden) return false;

        const ativo = document.activeElement;
        const digitando = ativo && ["INPUT", "TEXTAREA", "SELECT"].includes(ativo.tagName);
        if (digitando) return false;

        return !document.querySelector("form[data-sujo='true']");
    }

    setInterval(function () {
        if (podeAtualizar()) {
            window.location.reload();
        }
    }, INTERVALO_MS);
})();
